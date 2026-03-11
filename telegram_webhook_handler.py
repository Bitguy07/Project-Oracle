"""
telegram_webhook_handler.py
Called by GitHub Actions when a repository_dispatch event is received.
Reads the Telegram update from TELEGRAM_UPDATE env var and processes it.

This script bridges the gap between:
  Telegram → (your relay service/Cloudflare Worker) → GitHub API → Actions → this script
"""

import asyncio
import json
import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("oracle.webhook")


async def main():
    raw = os.environ.get("TELEGRAM_UPDATE")
    if not raw:
        log.error("TELEGRAM_UPDATE env var not set.")
        sys.exit(1)

    try:
        update = json.loads(raw)
    except json.JSONDecodeError as e:
        log.error(f"Failed to parse TELEGRAM_UPDATE: {e}")
        sys.exit(1)

    log.info(f"Processing Telegram update: {json.dumps(update)[:200]}")

    from core.telegram_bot import TelegramBot
    bot = TelegramBot()
    await bot.handle_update(update)


if __name__ == "__main__":
    asyncio.run(main())
