import argparse
import sys
import logging
from web_api.server import WebServer
from common.constants import DEFAULT_GATEWAY_HOST, DEFAULT_GATEWAY_CLIENT_PORT

def main():
    parser = argparse.ArgumentParser(description="NetCourier Web Client & API Server")
    parser.add_argument("--gateway-host", default=DEFAULT_GATEWAY_HOST, help="Gateway host address")
    parser.add_argument("--gateway-port", type=int, default=DEFAULT_GATEWAY_CLIENT_PORT, help="Gateway client port")
    parser.add_argument("--host", default="127.0.0.1", help="Host address to run the Web Server on")
    parser.add_argument("--port", type=int, default=8080, help="Port to run the Web Server on")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    
    args = parser.parse_args()
    
    logging_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=logging_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)]
    )
    
    # Enable debugging for underlying connection objects if requested
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
        
    logging.info(f"Starting Web Client Server on http://{args.host}:{args.port}...")
    logging.info(f"Targeting Gateway at {args.gateway_host}:{args.gateway_port}")
    
    server = WebServer(
        host=args.host,
        port=args.port,
        gateway_host=args.gateway_host,
        gateway_port=args.gateway_port
    )
    
    try:
        server.start()
    except KeyboardInterrupt:
        logging.info("Client server shutting down...")
    except Exception as e:
        logging.exception(f"Unexpected error: {e}")

if __name__ == "__main__":
    main()
