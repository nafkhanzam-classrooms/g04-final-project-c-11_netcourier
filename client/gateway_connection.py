import socket
import threading
import logging
import json
from common.protocol import send_packet, receive_packet, build_packet

class GatewayConnection:
    def __init__(self, host, port, app):
        self.host = host
        self.port = port
        self.app = app
        self.logger = logging.getLogger("GatewayConnection")
        
        self.sock = None
        self.running = False
        self.receiver_thread = None
        
        # Keep track of pending requests to match responses
        self.pending_requests = {}
        self.lock = threading.Lock()

    def connect(self):
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((self.host, self.port))
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.running = True
            
            self.receiver_thread = threading.Thread(target=self._receive_loop, daemon=True)
            self.receiver_thread.start()
            self.logger.info(f"Connected to Gateway at {self.host}:{self.port}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to connect to Gateway: {e}")
            return False

    def disconnect(self):
        self.running = False
        if self.sock:
            try:
                self.sock.close()
            except:
                pass
        self.logger.info("Disconnected from Gateway")

    def send_request(self, msg_type, payload=None, callback=None):
        if not self.running:
            return False
            
        header = build_packet(msg_type, payload, token=self.app.token)
        req_id = header["request_id"]
        
        if callback:
            with self.lock:
                self.pending_requests[req_id] = callback
            
        try:
            send_packet(self.sock, header)
            return True
        except Exception as e:
            self.logger.error(f"Error sending request {msg_type}: {e}")
            with self.lock:
                if req_id in self.pending_requests:
                    self.pending_requests.pop(req_id)
            self.running = False
            return False

    def _receive_loop(self):
        try:
            while self.running:
                header, payload_bytes = receive_packet(self.sock)
                self._handle_incoming_packet(header, payload_bytes)
        except (ConnectionError, socket.error):
            self.logger.info("Connection to Gateway closed")
        except Exception as e:
            self.logger.exception(f"Error in receiver loop: {e}")
        finally:
            self.running = False
            self.app.run_in_ui(self.app.on_gateway_disconnected)

    def _handle_incoming_packet(self, header, payload_bytes):
        msg_type = header.get("type")
        req_id = header.get("request_id")
        payload = header.get("payload", {})
        
        self.logger.debug(f"Received {msg_type} from Gateway")
        
        # Check if this is a response to a pending request
        callback = None
        with self.lock:
            if req_id in self.pending_requests:
                callback = self.pending_requests.pop(req_id)
        
        if callback:
            self.app.run_in_ui(callback, header)
            return

        # Handle unsolicited events (Phase 3+)
        if msg_type == "PRIVATE_MESSAGE_RECEIVED":
            self.app.run_in_ui(self.app.on_pm_received, payload)
        elif msg_type == "ERROR":
            error_msg = payload.get("message", "Unknown error")
            self.app.run_in_ui(self.app.show_error, f"Gateway Error: {error_msg}")
