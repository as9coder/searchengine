import asyncio
import logging
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.crawler import Crawler
from src.database import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


async def main():
    await init_db()
    crawler = Crawler()
    try:
        await crawler.run()
    finally:
        await crawler.close()


if __name__ == "__main__":
    asyncio.run(main())
