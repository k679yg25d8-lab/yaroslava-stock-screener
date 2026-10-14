# Yaroslava Stock Screener

Nasdaq stocks, $1–25, accumulated premarket volume >300,000 and any premarket minute High reaching its SMA(close,10) ×1.10. Premarket: 04:00–09:30 America/New_York. Polygon REST only; no WebSocket. Charts, browser-local day history, and read-only Telegram ticker import.

## Render
Create a Python Web Service from this repository. Choose **Free**. Build: `pip install -r requirements.txt`. Start: `gunicorn web:app --bind 0.0.0.0:$PORT --workers 1 --threads 8 --timeout 120 --access-logfile /dev/null`. Health check: `/api/health`.

API keys and Telegram tokens are entered through the HTTPS website and held only in server RAM, isolated per browser session. No credentials are included in this repository. After restart or 8 hours of inactivity, re-enter credentials. A Telegram bot should have only one active polling client. Day history is stored in your browser, not shared across devices. Export important history from the day journal.

The Free instance sleeps after 15 minutes without traffic and may take about a minute to wake up. It is suitable for a personal preview, not uninterrupted market monitoring. Do not keep it awake with artificial requests.
