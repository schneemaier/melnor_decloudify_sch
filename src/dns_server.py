import logging
from dnslib import DNSRecord, QTYPE, RR, A
from dnslib.server import DNSServer, BaseResolver
import socket
from src.settings import settings

logger = logging.getLogger("DNS")

class MelnorResolver(BaseResolver):
    def resolve(self, request, handler):
        qname = str(request.q.qname)
        qtype = QTYPE[request.q.qtype]

        reply = request.reply()

        if settings.enabled:
            if "wifiaquatimer.com" in qname or "ws.pusherapp.com" in qname:
                logger.info(f"Spoofing DNS for {qname} to {settings.myIP}")
                reply.add_answer(RR(qname, QTYPE.A, rdata=A(settings.myIP), ttl=1800))
                return reply

        # Forwarding logic (simple implementation)
        try:
            if settings.disableDNS:
                logger.info("DNS spoofing disabled. Forwarding request.")
            else:
                 logger.debug(f"Forwarding DNS request for {qname}")

            # Use socket to query upstream DNS
            proxy_r = request.send(settings.dnsForwarder, 53)
            reply = DNSRecord.parse(proxy_r)
        except Exception as e:
            logger.error(f"Error forwarding DNS request: {e}")

        return reply

def start_dns_server(port=53, address="0.0.0.0"):
    resolver = MelnorResolver()
    server = DNSServer(resolver, port=port, address=address)
    logger.info(f"Starting DNS server on {address}:{port}")
    server.start_thread()
    return server

if __name__ == "__main__":
    import time
    server = start_dns_server(port=5353) # Use 5353 for testing
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
