"""
Simple scheduler — runs poll_and_process() every 30 seconds.
Used as the entry point on Railway/Render.
"""

import time
import logging
from main import poll_and_process

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

INTERVAL_SECONDS = 30  # 30 seconds

if __name__ == "__main__":
    log.info("🚀 Growth Hirings Tracker started. Polling every 30 seconds.")
    while True:
        try:
            poll_and_process()
        except Exception as e:
            log.error(f"Error during poll: {e}", exc_info=True)
        log.info(f"Sleeping {INTERVAL_SECONDS}s until next poll...")
        time.sleep(INTERVAL_SECONDS)
