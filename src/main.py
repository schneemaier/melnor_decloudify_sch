import asyncio
import logging
import sys
from src.settings import settings
from src.dns_server import start_dns_server
from src.web_server import start_web_server

# Setup logging
logging.basicConfig(
    level=getattr(logging, settings.loglevel, logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger("MAIN")

async def main():
    logger.info("Melnor Aqua Timer Decloudifier starting...")

    # Start DNS Server
    dns_port = 53
    dns_server = None
    try:
        # Use bindDNS setting or fallback to 0.0.0.0
        bind_address = settings.bindDNS if settings.bindDNS else "0.0.0.0"

        # In the original implementation, if settings.disableDNS is true, it doesn't start the server.
        if not settings.disableDNS:
            dns_server = start_dns_server(port=dns_port, address=bind_address)
        else:
            logger.info("DNS spoofing disabled by settings.")

    except PermissionError:
        logger.error(f"Failed to bind DNS server to port {dns_port}. Root privileges are usually required for port 53.")
        # We continue without DNS server as per review suggestion to handle it gracefully,
        # but for a "Decloudifier" this is critical. However, if the user only wants REST API and
        # manages DNS externally (as mentioned in README via dnsmasq), this is valid.
        logger.warning("Continuing without internal DNS server. Ensure external DNS redirection is configured.")
    except Exception as e:
        logger.error(f"Failed to start DNS server: {e}")
        # Continue without DNS

    # Start Web Server
    try:
        runner, app = await start_web_server()
    except Exception as e:
        logger.critical(f"Failed to start Web server: {e}")
        if dns_server:
            dns_server.stop()
        sys.exit(1)

    # Keep running
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        if dns_server:
            dns_server.stop()
        await runner.cleanup()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
