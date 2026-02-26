import asyncio
import logging
import sys
from settings import settings
from valveSettings import valveSettings
from web_server import start_web_server

# Setup logging
logging.basicConfig(
    level=getattr(logging, settings.loglevel, logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger("MAIN")

async def main():
    logger.info("Melnor Aqua Timer Decloudifier starting...")

    try:
        runner, app = await start_web_server()
    except Exception as e:
        logger.critical(f"Failed to start Web server: {e}")
        sys.exit(1)

    # Keep running
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        await runner.cleanup()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
