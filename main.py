# ---------- Configuration ----------
BOT_TOKEN = os.environ.get("BOT_TOKEN")

def parse_admin_ids(raw: str) -> list[int]:
    """Safely parse comma-separated numeric admin IDs. Never crashes."""
    ids = []
    for item in raw.split(","):
        item = item.strip().strip('"').strip("'")  # tolerate quoted values
        if not item:
            continue
        try:
            ids.append(int(item))
        except ValueError:
            logger.error(f"Ignoring invalid ADMIN_IDS entry: {item!r} (must be numeric Telegram user ID)")
    return ids

ADMIN_IDS = parse_admin_ids(os.environ.get("ADMIN_IDS", ""))
CHANNEL_ID = os.environ.get("CHANNEL_ID")
CHANNEL_URL = os.environ.get("CHANNEL_URL", "https://t.me/spotyglobalnews")
NEWS_INTERVAL_MINUTES = int(os.environ.get("NEWS_INTERVAL_MINUTES", "60"))
AUTO_PUBLISH = os.environ.get("AUTO_PUBLISH", "true").lower() == "true"
DB_PATH = os.environ.get("DB_PATH", "/app/data/spoty.db")
