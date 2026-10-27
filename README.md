# Spoty Global News Bot

Telegram bot for music and Spotify news community.  
Bot: [@Spotyglobalnewsbot](https://t.me/Spotyglobalnewsbot)

## Features

- Commands: /start, /help, /news, /trending, /about, /channel, /subscribe, /unsubscribe, /status, /admin
- Inline keyboard interface
- RSS-based news aggregation (legitimate music news sources)
- Automatic publishing to channel (configurable interval, can be disabled)
- Duplicate prevention via SQLite
- Admin panel: stats, feed testing, auto-publish toggle, broadcast
- Persistent storage via SQLite + Railway Volume

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `BOT_TOKEN` | ✅ | Telegram bot token from @BotFather |
| `ADMIN_IDS` | ✅ | Comma-separated admin user IDs |
| `CHANNEL_ID` | ✅ | Channel username or ID for news publishing |
| `CHANNEL_URL` | ✅ | Public channel URL |
| `NEWS_INTERVAL_MINUTES` | ❌ | Publishing interval (default: 60) |
| `AUTO_PUBLISH` | ❌ | Enable auto-publish (default: true) |
| `DB_PATH` | ❌ | SQLite path (default: `/app/data/spoty.db`) |

## Deploy on Railway

### 1. Create Bot & Get Token
1. Open [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot`, set name and username (`@Spotyglobalnewsbot`)
3. Copy the **API Token**

### 2. Get Your Admin User ID
Send `/start` to [@userinfobot](https://t.me/userinfobot) to get your numeric Telegram ID.

### 3. Push to GitHub
```bash
git init
git add .
git commit -m "init spoty news bot"
git remote add origin <your-repo-url>
git push -u origin main
