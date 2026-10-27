import os
import sqlite3
import logging
import asyncio
import feedparser
from datetime import datetime, timedelta
from typing import Optional
from contextlib import closing

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ---------- Configuration ----------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_IDS = [int(x.strip()) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()]
CHANNEL_ID = os.environ.get("CHANNEL_ID")          # e.g. @spotyglobalnews
CHANNEL_URL = os.environ.get("CHANNEL_URL", "https://t.me/spotyglobalnews")
NEWS_INTERVAL_MINUTES = int(os.environ.get("NEWS_INTERVAL_MINUTES", "60"))
AUTO_PUBLISH = os.environ.get("AUTO_PUBLISH", "true").lower() == "true"
DB_PATH = os.environ.get("DB_PATH", "/app/data/spoty.db")  # Railway volume mount path

# ---------- Logging ----------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ---------- Database ----------
def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS subscribers (
                user_id INTEGER PRIMARY KEY,
                subscribed_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS seen_articles (
                article_id TEXT PRIMARY KEY,
                title TEXT,
                url TEXT,
                seen_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        conn.execute("""
            INSERT OR IGNORE INTO settings (key, value) VALUES ('auto_publish', ?)
        """, ("1" if AUTO_PUBLISH else "0",))
        conn.execute("""
            INSERT OR IGNORE INTO settings (key, value) VALUES ('last_news_update', 'Never')
        """)
        conn.commit()


def get_setting(key: str, default: str = "") -> str:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default


def set_setting(key: str, value: str):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        conn.commit()


def add_subscriber(user_id: int):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("INSERT OR IGNORE INTO subscribers (user_id) VALUES (?)", (user_id,))
        conn.commit()


def remove_subscriber(user_id: int):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("DELETE FROM subscribers WHERE user_id = ?", (user_id,))
        conn.commit()


def is_subscribed(user_id: int) -> bool:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        row = conn.execute("SELECT 1 FROM subscribers WHERE user_id = ?", (user_id,)).fetchone()
        return row is not None


def get_all_subscribers() -> list[int]:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        return [r[0] for r in conn.execute("SELECT user_id FROM subscribers").fetchall()]


def article_seen(article_id: str) -> bool:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        row = conn.execute("SELECT 1 FROM seen_articles WHERE article_id = ?", (article_id,)).fetchone()
        return row is not None


def mark_article_seen(article_id: str, title: str, url: str):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO seen_articles (article_id, title, url) VALUES (?, ?, ?)",
            (article_id, title, url),
        )
        conn.commit()


# ---------- RSS Feed Sources (legitimate music news) ----------
RSS_FEEDS = [
    "https://pitchfork.com/feed/feed-news/rss",
    "https://www.billboard.com/feed/",
    "https://www.nme.com/feed",
    "https://consequence.net/feed/",
]


def fetch_news(max_per_feed: int = 5) -> list[dict]:
    """Fetch articles from RSS feeds, filter duplicates, return new articles."""
    new_articles = []
    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            if feed.bozo and not feed.entries:
                logger.warning(f"Feed malformed or empty: {feed_url}")
                continue
            for entry in feed.entries[:max_per_feed]:
                # Use entry id or link as unique identifier
                article_id = entry.get("id") or entry.get("link")
                if not article_id:
                    continue
                if article_seen(article_id):
                    continue
                title = entry.get("title", "No title")
                link = entry.get("link", "")
                summary = entry.get("summary", "")[:300]
                source = feed.feed.get("title", feed_url)
                new_articles.append({
                    "id": article_id,
                    "title": title,
                    "link": link,
                    "summary": summary,
                    "source": source,
                })
        except Exception as e:
            logger.error(f"Error fetching feed {feed_url}: {e}")
    return new_articles


def format_article(article: dict) -> str:
    """Format an article for Telegram posting."""
    text = f"🎵 <b>{article['title']}</b>\n\n"
    if article["summary"]:
        text += f"{article['summary']}\n\n"
    text += f"📰 Source: {article['source']}\n"
    if article["link"]:
        text += f"🔗 <a href='{article['link']}'>Read more</a>"
    return text


async def publish_news(context: ContextTypes.DEFAULT_TYPE):
    """Scheduled job: fetch and publish new articles to channel."""
    if get_setting("auto_publish", "1") != "1":
        logger.info("Auto-publish disabled, skipping.")
        return

    if not CHANNEL_ID:
        logger.warning("CHANNEL_ID not set, cannot publish news.")
        return

    articles = fetch_news()
    if not articles:
        logger.info("No new articles found.")
        return

    for article in articles[:5]:  # Limit per run to avoid spam
        try:
            await context.bot.send_message(
                chat_id=CHANNEL_ID,
                text=format_article(article),
                parse_mode="HTML",
                disable_web_page_preview=False,
            )
            mark_article_seen(article["id"], article["title"], article["link"])
            logger.info(f"Published: {article['title']}")
            await asyncio.sleep(2)  # Respect rate limits
        except Exception as e:
            logger.error(f"Failed to publish article: {e}")

    set_setting("last_news_update", datetime.now().strftime("%Y-%m-%d %H:%M UTC"))


# ---------- Command Handlers ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📰 Latest News", callback_data="news"),
         InlineKeyboardButton("🔥 Trending", callback_data="trending")],
        [InlineKeyboardButton("📢 Join Our Channel", url=CHANNEL_URL),
         InlineKeyboardButton("🔔 Subscribe", callback_data="subscribe")],
        [InlineKeyboardButton("❓ Help", callback_data="help")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🎧 <b>Welcome to Spoty Global News!</b>\n\n"
        "Your source for the latest Spotify, music industry, artist, album, and streaming news.\n\n"
        "Choose an option below or use /help to see all commands.",
        parse_mode="HTML",
        reply_markup=reply_markup,
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "<b>Available Commands:</b>\n\n"
        "/start - Welcome menu\n"
        "/news - Latest music news\n"
        "/trending - Trending stories\n"
        "/about - About this bot\n"
        "/channel - Join our Telegram channel\n"
        "/subscribe - Get news notifications\n"
        "/unsubscribe - Stop notifications\n"
        "/status - Bot status\n"
        "/help - This message\n\n"
        "<i>Admins:</i> /admin"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def news_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📡 Fetching latest news, please wait...")
    articles = fetch_news(max_per_feed=3)
    if not articles:
        await update.message.reply_text("No new articles right now. Try again later.")
        return
    for article in articles[:3]:
        await update.message.reply_text(
            format_article(article),
            parse_mode="HTML",
            disable_web_page_preview=False,
        )
        await asyncio.sleep(1)


async def trending_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔥 <b>Trending Music Stories</b>\n\n"
        "Trending data is currently being curated. Check back soon or visit our channel for updates.",
        parse_mode="HTML",
    )


async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 <b>About Spoty Global News</b>\n\n"
        "This bot delivers the latest music industry news, Spotify updates, artist and album releases, "
        "and streaming trends from legitimate sources.\n\n"
        "We do not invent news or claim partnerships with Spotify. All content is attributed to its original source.",
        parse_mode="HTML",
    )


async def channel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"📢 Join our official channel: {CHANNEL_URL}",
        disable_web_page_preview=True,
    )


async def subscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_subscribed(user_id):
        await update.message.reply_text("✅ You are already subscribed to news updates.")
        return
    add_subscriber(user_id)
    await update.message.reply_text(
        "🔔 <b>Subscribed!</b>\nYou will receive news notifications when new articles are published.",
        parse_mode="HTML",
    )


async def unsubscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_subscribed(user_id):
        await update.message.reply_text("You are not currently subscribed.")
        return
    remove_subscriber(user_id)
    await update.message.reply_text("🔕 You have been unsubscribed from news notifications.")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    auto = "Enabled" if get_setting("auto_publish", "1") == "1" else "Disabled"
    last = get_setting("last_news_update", "Never")
    subs = len(get_all_subscribers())
    await update.message.reply_text(
        f"📊 <b>Bot Status</b>\n\n"
        f"🟢 Running\n"
        f"📰 Auto-publish: {auto}\n"
        f"🕐 Last news update: {last}\n"
        f"👥 Subscribers: {subs}\n"
        f"⏱ Interval: {NEWS_INTERVAL_MINUTES} minutes",
        parse_mode="HTML",
    )


# ---------- Admin ----------
def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            await update.message.reply_text("⛔ Unauthorized. This command is for admins only.")
            return
        return await func(update, context)
    return wrapper


@admin_only
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    auto = get_setting("auto_publish", "1")
    keyboard = [
        [InlineKeyboardButton("📊 Stats", callback_data="admin_stats"),
         InlineKeyboardButton("📰 Test Feed", callback_data="admin_test_feed")],
        [InlineKeyboardButton(
            f"🔄 Auto-publish: {'ON' if auto == '1' else 'OFF'}",
            callback_data="admin_toggle_auto",
        )],
        [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
    ]
    await update.message.reply_text(
        "🔧 <b>Admin Panel</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await query.edit_message_text("⛔ Unauthorized.")
        return

    data = query.data
    if data == "admin_stats":
        subs = len(get_all_subscribers())
        auto = "ON" if get_setting("auto_publish", "1") == "1" else "OFF"
        last = get_setting("last_news_update", "Never")
        await query.edit_message_text(
            f"📊 <b>Statistics</b>\n\n"
            f"Subscribers: {subs}\n"
            f"Auto-publish: {auto}\n"
            f"Last update: {last}\n"
            f"Interval: {NEWS_INTERVAL_MINUTES} min",
            parse_mode="HTML",
        )
    elif data == "admin_test_feed":
        await query.edit_message_text("🔄 Testing feed...")
        articles = fetch_news(max_per_feed=1)
        if articles:
            await query.edit_message_text(
                f"✅ Feed working. Sample:\n\n{format_article(articles[0])}",
                parse_mode="HTML",
            )
        else:
            await query.edit_message_text("⚠️ No new articles found or feed error.")
    elif data == "admin_toggle_auto":
        current = get_setting("auto_publish", "1")
        new_val = "0" if current == "1" else "1"
        set_setting("auto_publish", new_val)
        await query.edit_message_text(
            f"✅ Auto-publish now: {'ON' if new_val == '1' else 'OFF'}"
        )
    elif data == "admin_broadcast":
        await query.edit_message_text(
            "📢 To broadcast, use: <code>/broadcast Your message here</code>",
            parse_mode="HTML",
        )


@admin_only
async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Broadcast a message to all subscribers with confirmation."""
    if not context.args:
        await update.message.reply_text("Usage: /broadcast Your message text")
        return
    message_text = " ".join(context.args)
    subscribers = get_all_subscribers()
    if not subscribers:
        await update.message.reply_text("No subscribers to broadcast to.")
        return

    # Confirmation via inline keyboard
    context.user_data["broadcast_text"] = message_text
    keyboard = [[
        InlineKeyboardButton("✅ Confirm Send", callback_data="broadcast_confirm"),
        InlineKeyboardButton("❌ Cancel", callback_data="broadcast_cancel"),
    ]]
    await update.message.reply_text(
        f"📢 <b>Confirm broadcast</b>\n\n"
        f"Recipients: {len(subscribers)}\n"
        f"Message: {message_text[:200]}...",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def broadcast_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if update.effective_user.id not in ADMIN_IDS:
        return

    if query.data == "broadcast_cancel":
        await query.edit_message_text("❌ Broadcast cancelled.")
        return

    text = context.user_data.pop("broadcast_text", None)
    if not text:
        await query.edit_message_text("⚠️ No message to send.")
        return

    subscribers = get_all_subscribers()
    sent, failed = 0, 0
    await query.edit_message_text("📤 Sending broadcast...")
    for uid in subscribers:
        try:
            await context.bot.send_message(chat_id=uid, text=text)
            sent += 1
            await asyncio.sleep(0.05)  # Rate limit
        except Exception:
            failed += 1
    await query.edit_message_text(f"✅ Broadcast complete.\nSent: {sent}\nFailed: {failed}")


# ---------- Generic Callback Handler for user menu ----------
async def user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "news":
        await query.edit_message_text("📡 Fetching news...")
        articles = fetch_news(max_per_feed=3)
        if articles:
            text = "\n\n".join([format_article(a) for a in articles[:3]])
            await query.edit_message_text(text, parse_mode="HTML")
        else:
            await query.edit_message_text("No new articles available right now.")
    elif data == "trending":
        await query.edit_message_text(
            "🔥 Trending stories are being curated. Check back soon!",
        )
    elif data == "subscribe":
        user_id = update.effective_user.id
        if is_subscribed(user_id):
            await query.edit_message_text("✅ You are already subscribed.")
        else:
            add_subscriber(user_id)
            await query.edit_message_text("🔔 Subscribed to news updates!")
    elif data == "help":
        await query.edit_message_text(
            "Use /help to see all commands.",
        )


# ---------- Error Handler ----------
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error: {context.error}")


# ---------- Main ----------
def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN environment variable is required")
    if not ADMIN_IDS:
        logger.warning("ADMIN_IDS is empty. Admin commands will be inaccessible.")

    init_db()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("news", news_command))
    app.add_handler(CommandHandler("trending", trending_command))
    app.add_handler(CommandHandler("about", about_command))
    app.add_handler(CommandHandler("channel", channel_command))
    app.add_handler(CommandHandler("subscribe", subscribe_command))
    app.add_handler(CommandHandler("unsubscribe", unsubscribe_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))

    # Callback handlers
    app.add_handler(CallbackQueryHandler(user_callback, pattern="^(news|trending|subscribe|help)$"))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^admin_"))
    app.add_handler(CallbackQueryHandler(broadcast_callback, pattern="^broadcast_"))

    # Error handler
    app.add_error_handler(error_handler)

    # Schedule news publishing
    job_queue = app.job_queue
    job_queue.run_repeating(
        publish_news,
        interval=timedelta(minutes=NEWS_INTERVAL_MINUTES),
        first=10,  # Start after 10 seconds
        name="news_publisher",
    )

    logger.info(f"Bot starting (interval: {NEWS_INTERVAL_MINUTES} min, auto-publish: {AUTO_PUBLISH})")
    app.run_polling()


if __name__ == "__main__":
    main()
