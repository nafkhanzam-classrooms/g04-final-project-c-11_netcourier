import socket
import threading
import logging
from common.protocol import send_packet, receive_packet, build_packet

class RoomConnection:
    def __init__(self, host, port, app):
        self.host = host
        self.port = port
        self.app = app
        self.logger = logging.getLogger("RoomConnection")
        
        self.sock = None
        self.running = False
        self.receiver_thread = None
        self.current_room = None
        
        self.pending_requests = {}
        self.lock = threading.Lock()
        self.write_lock = threading.Lock()

    def connect(self):
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((self.host, self.port))
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.running = True
            
            self.receiver_thread = threading.Thread(target=self._receive_loop, daemon=True)
            self.receiver_thread.start()
            self.logger.info(f"Connected to Process Server at {self.host}:{self.port}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to connect to Process Server: {e}")
            return False

    def disconnect(self):
        self.running = False
        if self.sock:
            try:
                self.sock.close()
            except:
                pass
        self.logger.info("Disconnected from Process Server")

    def send_request(self, msg_type, payload=None, callback=None):
        if not self.running:
            return False
            
        header = build_packet(msg_type, payload, token=self.app.token)
        req_id = header["request_id"]
        
        if callback:
            with self.lock:
                self.pending_requests[req_id] = callback
            
        try:
            with self.write_lock:
                send_packet(self.sock, header)
            return True
        except Exception as e:
            self.logger.error(f"Error sending room request {msg_type}: {e}")
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
            self.logger.info("Connection to Process Server closed")
        except Exception as e:
            self.logger.exception(f"Error in room receiver loop: {e}")
        finally:
            self.running = False
            self.app.run_in_ui(self.app.on_room_disconnected)

    def _handle_incoming_packet(self, header, payload_bytes):
        msg_type = header.get("type")
        req_id = header.get("request_id")
        payload = header.get("payload", {})
        
        self.logger.debug(f"Received {msg_type} from Process Server")
        
        callback = None
        with self.lock:
            if req_id in self.pending_requests:
                callback = self.pending_requests.pop(req_id)
        
        if callback:
            self.app.run_in_ui(callback, header)
            return

        # Room events (Phase 6+)
        if msg_type == "DOWNLOAD_CHUNK":
            if hasattr(self.app, "active_downloader") and self.app.active_downloader:
                chunk_index = payload.get("chunk_index")
                self.app.active_downloader.handle_chunk(chunk_index, payload_bytes)
            return

        if msg_type == "ROOM_CHAT_BROADCAST":
            self.app.run_in_ui(self.app.on_room_message, payload)
        elif msg_type == "ROOM_DELETE_FILE_BROADCAST":
            if hasattr(self.app, "on_room_delete_file"):
                self.app.run_in_ui(self.app.on_room_delete_file, payload)
        elif msg_type == "ROOM_REACTION_BROADCAST":
            self.app.run_in_ui(self.app.on_room_reaction, payload)
        elif msg_type == "ROOM_TYPING_BROADCAST":
            self.app.run_in_ui(self.app.on_room_typing, payload)
        elif msg_type == "SYSTEM_EVENT":
            self.app.run_in_ui(self.app.on_room_system_event, payload)
        elif msg_type == "ROOM_HISTORY_RESPONSE":
            self.app.run_in_ui(self.app.on_room_history_response, payload)
        elif msg_type == "ERROR":
            error_msg = payload.get("message", "Unknown room error")
            self.app.run_in_ui(self.app.show_error, f"Room Error: {error_msg}")
