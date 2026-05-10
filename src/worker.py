import asyncio
import json
import logging
import signal
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.crawler import Crawler
from src.database import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

_shutdown_requested = False


def handle_signal(sig, frame):
    global _shutdown_requested
    logger.info(f"Received signal {sig}, shutting down gracefully...")
    _shutdown_requested = True


async def main():
    await init_db()
    crawler = Crawler()
    
    # Register signal handlers (Linux/macOS VPS)
    try:
        signal.signal(signal.SIGTERM, handle_signal)
        signal.signal(signal.SIGINT, handle_signal)
    except ValueError:
        pass  # Windows: signals only work in main thread
    
    try:
        await crawler.run(shutdown_flag=lambda: _shutdown_requested)
    except asyncio.CancelledError:
        logger.info("Crawler cancelled")
    finally:
        await crawler.save_state()
        await crawler.close()
        logger.info("Worker shut down cleanly")


if __name__ == "__main__":
    asyncio.run(main())
