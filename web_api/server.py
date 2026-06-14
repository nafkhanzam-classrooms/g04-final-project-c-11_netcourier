import socket
import threading
import json
import uuid
import time
import os
import urllib.parse
from client.gateway_connection import GatewayConnection
from client.room_connection import RoomConnection
from common.constants import DEFAULT_GATEWAY_HOST, DEFAULT_GATEWAY_CLIENT_PORT

WEB_UI_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web_ui")

class MockApp:
    def __init__(self, session):
        self.session = session
        self.token = None
        
    def run_in_ui(self, callback, *args, **kwargs):
        try:
            callback(*args, **kwargs)
        except Exception as e:
            print(f"Callback error: {e}")

    def on_pm_received(self, payload):
        self.session.push_event({"type": "PM_RECEIVED", "payload": payload})
        
    def on_room_message(self, payload):
        self.session.push_event({"type": "ROOM_MESSAGE", "payload": payload})
        
    def on_room_delete_file(self, payload):
        self.session.push_event({"type": "ROOM_DELETE_FILE_BROADCAST", "payload": payload})
        
    def on_room_reaction(self, payload):
        self.session.push_event({"type": "ROOM_REACTION_BROADCAST", "payload": payload})
        
    def on_room_typing(self, payload):
        self.session.push_event({"type": "ROOM_TYPING_BROADCAST", "payload": payload})
        
    def on_room_member_list_response(self, payload):
        # This is a response to a request, but we can also use it for real-time updates if needed
        self.session.push_event({"type": "ROOM_MEMBER_LIST", "payload": payload})
        
    def on_room_system_event(self, payload):
        self.session.push_event({"type": "SYSTEM_EVENT", "payload": payload})
        
    def on_gateway_disconnected(self):
        self.session.push_event({"type": "DISCONNECTED", "server": "gateway"})
        
    def on_room_disconnected(self):
        self.session.push_event({"type": "DISCONNECTED", "server": "room"})
        
    def show_error(self, message):
        self.session.push_event({"type": "ERROR", "message": message})

class WebSession:
    def __init__(self, session_id, gateway_host=DEFAULT_GATEWAY_HOST, gateway_port=DEFAULT_GATEWAY_CLIENT_PORT):
        self.session_id = session_id
        self.app = MockApp(self)
        self.gateway_conn = GatewayConnection(gateway_host, gateway_port, self.app)
        self.room_conn = None
        self.events = []
        self.lock = threading.Lock()
        self.cond = threading.Condition(self.lock)
        self.username = None
        
        self.gateway_conn.connect()

    def push_event(self, event):
        with self.cond:
            self.events.append(event)
            self.cond.notify_all()

    def get_events(self, timeout=30):
        with self.cond:
            if not self.events:
                self.cond.wait(timeout)
            events_to_return = self.events[:]
            self.events = []
            return events_to_return

class WebServer:
    def __init__(self, host='0.0.0.0', port=8080, gateway_host=DEFAULT_GATEWAY_HOST, gateway_port=DEFAULT_GATEWAY_CLIENT_PORT):
        self.host = host
        self.port = port
        self.gateway_host = gateway_host
        self.gateway_port = gateway_port
        self.sessions = {}
        self.running = False
        
    def start(self):
        self.running = True
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen(100)
        print(f"Web UI & API Server running at http://{self.host}:{self.port}")
        
        while self.running:
            try:
                conn, addr = self.server_socket.accept()
                threading.Thread(target=self.handle_client, args=(conn, addr), daemon=True).start()
            except Exception as e:
                if self.running:
                    print(f"Accept error: {e}")

    def handle_client(self, conn, addr):
        try:
            try:
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except Exception as e:
                pass
            req = b""
            # Increase header reading buffer and limit (1GB body limit)
            MAX_HEADER_SIZE = 65536
            while b"\r\n\r\n" not in req:
                chunk = conn.recv(8192)
                if not chunk:
                    break
                req += chunk
                if len(req) > MAX_HEADER_SIZE:
                    break
            
            if not req or b"\r\n\r\n" not in req:
                conn.close()
                return

            headers_part, body_part = req.split(b"\r\n\r\n", 1)
            lines = headers_part.decode('utf-8', errors='ignore').split("\r\n")
            
            request_line = lines[0]
            parts = request_line.split(" ")
            if len(parts) < 2:
                conn.close()
                return
                
            method, path = parts[0], parts[1]
            
            headers = {}
            for line in lines[1:]:
                if ":" in line:
                    k, v = line.split(":", 1)
                    headers[k.strip().lower()] = v.strip()
                    
            content_length = int(headers.get("content-length", 0))
            
            # Optimization: 1GB Body Limit
            if content_length > 1024 * 1024 * 1024:
                self.send_response(conn, 413, {"error": "Request entity too large"})
                return

            body = body_part
            # Fast body reading with up to 1MB chunks to minimize recv calls and memory copies
            while len(body) < content_length:
                chunk = conn.recv(min(1024 * 1024, content_length - len(body)))
                if not chunk:
                    break
                body += chunk

            self.route_request(conn, method, path, headers, body)
        except Exception as e:
            print(f"Error handling HTTP client: {e}")
        finally:
            try:
                conn.close()
            except:
                pass

    def send_response(self, conn, status_code, body, content_type="application/json"):
        if isinstance(body, dict) or isinstance(body, list):
            body = json.dumps(body).encode('utf-8')
        elif isinstance(body, str):
            body = body.encode('utf-8')
            
        status_text = "OK" if status_code == 200 else "Bad Request" if status_code == 400 else "Internal Server Error"
        response = f"HTTP/1.1 {status_code} {status_text}\r\n"
        response += f"Content-Type: {content_type}\r\n"
        response += "Access-Control-Allow-Origin: *\r\n"
        response += "Access-Control-Allow-Headers: Content-Type, Authorization, Session-Id\r\n"
        response += f"Content-Length: {len(body)}\r\n"
        response += "\r\n"
        try:
            conn.sendall(response.encode('utf-8') + body)
        except Exception as e:
            pass

    def serve_static(self, conn, path):
        if path == "/":
            path = "/index.html"
        file_path = os.path.join(WEB_UI_DIR, path.lstrip("/"))
        if not os.path.exists(file_path):
            self.send_response(conn, 404, "Not Found", "text/plain")
            return
            
        ext = os.path.splitext(file_path)[1]
        content_types = {
            ".html": "text/html",
            ".js": "application/javascript",
            ".css": "text/css",
            ".json": "application/json",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".svg": "image/svg+xml"
        }
        ct = content_types.get(ext, "application/octet-stream")
        
        with open(file_path, "rb") as f:
            body = f.read()
            self.send_response(conn, 200, body, ct)

    def get_session(self, headers, qs_params=None):
        session_id = headers.get("session-id")
        if not session_id and qs_params and "session_id" in qs_params:
            session_id = qs_params["session_id"]
        if session_id and session_id in self.sessions:
            return self.sessions[session_id]
        return None

    def route_request(self, conn, method, path, headers, body):
        if method == "OPTIONS":
            self.send_response(conn, 200, "")
            return

        parsed_url = urllib.parse.urlparse(path)
        path_only = parsed_url.path
        qs_params = dict(urllib.parse.parse_qsl(parsed_url.query))
        
        for k, v in qs_params.items():
            if isinstance(v, list) and len(v) == 1:
                qs_params[k] = v[0]

        if path_only.startswith("/api/"):
            # Bypass UTF-8 body decoding for binary files/upload chunks to optimize performance
            is_upload = (path_only == "/api/rooms/files/upload")
            json_body = {}
            if body and not is_upload:
                try:
                    json_body = json.loads(body.decode('utf-8'))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    json_body = {}
                
            self.handle_api(conn, method, path_only, headers, json_body, qs_params, raw_body=body)
        else:
            self.serve_static(conn, path_only)

    def _sync_request(self, conn_obj, msg_type, payload):
        if not conn_obj:
            return None
        ev = threading.Event()
        res_container = {}
        def cb(header):
            res_container["header"] = header
            ev.set()
        
        # Ensure we don't block forever if sending fails
        try:
            if not conn_obj.send_request(msg_type, payload, callback=cb):
                return None
        except Exception as e:
            print(f"Sync request send error: {e}")
            return None
            
        # Wait with a shorter, safer timeout
        if not ev.wait(timeout=5):
            print(f"Sync request {msg_type} timed out")
            return None
        return res_container.get("header")

    def handle_api(self, conn, method, path, headers, body, qs, raw_body=b""):
        session = self.get_session(headers, qs)
        
        if path == "/api/register" and method == "POST":
            # Temporarily use a fresh session to register
            try:
                temp_session = WebSession("temp-" + str(uuid.uuid4()), self.gateway_host, self.gateway_port)
                resp = self._sync_request(temp_session.gateway_conn, "REGISTER", body)
                temp_session.gateway_conn.disconnect()
                if resp and resp.get("type") == "REGISTER_OK":
                    self.send_response(conn, 200, {"success": True, "username": resp["payload"]["username"]})
                else:
                    self.send_response(conn, 400, {"success": False, "error": resp.get("type") if resp else "TIMEOUT"})
            except Exception as e:
                self.send_response(conn, 500, {"error": str(e)})
            return

        if path == "/api/login" and method == "POST":
            session_id = str(uuid.uuid4())
            try:
                new_session = WebSession(session_id, self.gateway_host, self.gateway_port)
                resp = self._sync_request(new_session.gateway_conn, "LOGIN", body)
                if resp and resp.get("type") == "LOGIN_OK":
                    new_session.app.token = resp.get("token")
                    new_session.username = body.get("username")
                    self.sessions[session_id] = new_session
                    self.send_response(conn, 200, {
                        "success": True, 
                        "session_id": session_id, 
                        "user": resp["payload"]
                    })
                else:
                    new_session.gateway_conn.disconnect()
                    self.send_response(conn, 400, {"success": False, "error": resp.get("payload", {}).get("message") if resp else "Gateway timeout"})
            except Exception as e:
                self.send_response(conn, 500, {"error": f"Login failed: {e}"})
            return

        if not session:
            self.send_response(conn, 401, {"error": "Unauthorized"})
            return

        if path == "/api/events" and method == "GET":
            events = session.get_events(timeout=25)
            self.send_response(conn, 200, {"events": events})
            return

        if path == "/api/users" and method == "GET":
            resp = self._sync_request(session.gateway_conn, "LIST_ONLINE_USERS", {})
            if resp and resp.get("type") == "ONLINE_USERS_RESPONSE":
                self.send_response(conn, 200, {"users": resp["payload"]["users"]})
            else:
                self.send_response(conn, 400, {"error": "Failed to get users"})
            return

        if path == "/api/pm" and method == "POST":
            session.gateway_conn.send_request("PRIVATE_MESSAGE_SEND", body)
            self.send_response(conn, 200, {"success": True})
            return

        if path == "/api/pm/history" and method == "GET":
            resp = self._sync_request(session.gateway_conn, "PM_HISTORY_REQUEST", {"other_username": qs.get("other_username")})
            if resp and resp.get("type") == "PM_HISTORY_RESPONSE":
                self.send_response(conn, 200, {"messages": resp["payload"]["messages"]})
            else:
                self.send_response(conn, 400, {"error": "Failed to get PM history"})
            return

        if path == "/api/rooms" and method == "GET":
            resp = self._sync_request(session.gateway_conn, "LIST_ROOMS", {})
            if resp and resp.get("type") == "ROOM_LIST_RESPONSE":
                self.send_response(conn, 200, {"rooms": resp["payload"]["rooms"]})
            else:
                self.send_response(conn, 400, {"error": "Failed to get rooms"})
            return

        if path == "/api/rooms" and method == "POST":
            resp = self._sync_request(session.gateway_conn, "CREATE_ROOM", body)
            if resp and resp.get("type") == "ROOM_ASSIGNED":
                self.send_response(conn, 200, {"room": resp["payload"]})
            else:
                self.send_response(conn, 400, {"error": "Failed to create room"})
            return

        if path == "/api/rooms/join" and method == "POST":
            room_name = body.get("room_name")
            print(f"DEBUG /api/rooms/join: joining room {room_name}")
            resp = self._sync_request(session.gateway_conn, "JOIN_ROOM", {"room_name": room_name})
            print(f"DEBUG JOIN_ROOM resp: {resp}")
            if resp and resp.get("type") == "ROOM_LOCATION":
                loc = resp["payload"]
                if session.room_conn:
                    session.room_conn.disconnect()
                
                print(f"DEBUG connecting to backend {loc['host']}:{loc['port']}")
                session.room_conn = RoomConnection(loc["host"], loc["port"], session.app)
                if session.room_conn.connect():
                    auth_resp = self._sync_request(session.room_conn, "AUTH_BACKEND", {})
                    print(f"DEBUG AUTH_BACKEND resp: {auth_resp}")
                    if auth_resp and auth_resp.get("type") == "AUTH_BACKEND_OK":
                        join_resp = self._sync_request(session.room_conn, "JOIN_ROOM_BACKEND", {"room_name": room_name})
                        print(f"DEBUG JOIN_ROOM_BACKEND resp: {join_resp}")
                        if join_resp and join_resp.get("type") == "JOIN_ROOM_OK":
                            self.send_response(conn, 200, {"success": True, "room_name": room_name})
                            return
                self.send_response(conn, 500, {"error": "Failed to join room backend"})
            else:
                self.send_response(conn, 400, {"error": "Failed to locate room", "details": resp})
            return

        if path == "/api/rooms/leave" and method == "POST":
            if session.room_conn:
                session.room_conn.send_request("LEAVE_ROOM", {})
                session.room_conn.disconnect()
                session.room_conn = None
            self.send_response(conn, 200, {"success": True})
            return

        if path == "/api/rooms/messages" and method == "GET":
            if session.room_conn:
                resp = self._sync_request(session.room_conn, "ROOM_HISTORY_REQUEST", {"room_name": qs.get("room_name")})
                if resp and resp.get("type") == "ROOM_HISTORY_RESPONSE":
                    self.send_response(conn, 200, {"messages": resp["payload"]["messages"]})
                    return
            self.send_response(conn, 400, {"error": "Failed to get room history"})
            return

        if path == "/api/rooms/members" and method == "GET":
            if session.room_conn:
                resp = self._sync_request(session.room_conn, "ROOM_MEMBER_LIST_REQUEST", {"room_name": qs.get("room_name")})
                if resp and resp.get("type") == "ROOM_MEMBER_LIST_RESPONSE":
                    self.send_response(conn, 200, {"members": resp["payload"]["members"]})
                    return
            self.send_response(conn, 400, {"error": "Failed to get room members"})
            return

        if path == "/api/rooms/messages" and method == "POST":
            if session.room_conn:
                session.room_conn.send_request("ROOM_CHAT_SEND", body)
                self.send_response(conn, 200, {"success": True})
            else:
                self.send_response(conn, 400, {"error": "Not connected to a room"})
            return

        if path == "/api/rooms/reactions" and method == "POST":
            if session.room_conn:
                session.room_conn.send_request("ROOM_MESSAGE_REACTION", body)
                self.send_response(conn, 200, {"success": True})
            else:
                self.send_response(conn, 400, {"error": "Not connected to a room"})
            return

        if path == "/api/rooms/typing" and method == "POST":
            if session.room_conn:
                session.room_conn.send_request("ROOM_TYPING_INDICATOR", body)
                self.send_response(conn, 200, {"success": True})
            else:
                self.send_response(conn, 400, {"error": "Not connected to a room"})
            return

        if path == "/api/rooms/kick" and method == "POST":
            if session.room_conn:
                session.room_conn.send_request("ROOM_KICK_USER", body)
                self.send_response(conn, 200, {"success": True})
            else:
                self.send_response(conn, 400, {"error": "Not connected to a room"})
            return

        if path == "/api/rooms/files/delete" and method == "POST":
            if session.room_conn:
                session.room_conn.send_request("ROOM_DELETE_FILE", body)
                self.send_response(conn, 200, {"success": True})
            else:
                self.send_response(conn, 400, {"error": "Not connected to a room"})
            return

        if path == "/api/rooms/files" and method == "GET":
            if session.room_conn:
                resp = self._sync_request(session.room_conn, "FILE_LIST_REQUEST", {"room_name": qs.get("room_name")})
                if resp and resp.get("type") == "FILE_LIST_RESPONSE":
                    self.send_response(conn, 200, {"files": resp["payload"]["files"]})
                    return
            self.send_response(conn, 400, {"error": "Failed to get file list"})
            return

        if path == "/api/rooms/files/resume" and method == "GET":
            if not session.room_conn:
                self.send_response(conn, 400, {"error": "Not connected to room"})
                return
            transfer_id = qs.get("transfer_id")
            direction = qs.get("direction", "upload")
            if not transfer_id:
                self.send_response(conn, 400, {"error": "Missing transfer_id"})
                return
            
            resp = self._sync_request(session.room_conn, "RESUME_TRANSFER", {
                "transfer_id": int(transfer_id),
                "direction": direction
            })
            if resp and resp.get("type") in ["UPLOAD_READY", "DOWNLOAD_READY"]:
                self.send_response(conn, 200, resp["payload"])
            else:
                self.send_response(conn, 400, {"error": "Resume failed", "details": resp})
            return

        if path == "/api/rooms/files/upload" and method == "POST":
            if not session.room_conn:
                self.send_response(conn, 400, {"error": "Not connected to room"})
                return
            room_name = qs.get("room_name")
            filename = qs.get("filename")
            action = qs.get("action")

            if not room_name or not filename:
                self.send_response(conn, 400, {"error": "Missing params"})
                return

            # Calculate dynamic chunk size to prevent port exhaustion (1MB to 16MB)
            chunk_size = 1024 * 1024
            filesize = int(qs.get("filesize", 0))
            if filesize > 100 * 1024 * 1024:
                import math
                mb = math.ceil(filesize / (100 * 1024 * 1024))
                chunk_size = min(16, mb) * 1024 * 1024


            # 1. Chunked Upload Flow
            if action:
                if action == "init":
                    filesize = int(qs.get("filesize", 0))
                    checksum = qs.get("checksum_sha256", "")
                    total_chunks = (filesize + chunk_size - 1) // chunk_size if filesize > 0 else 0
                    
                    init_resp = self._sync_request(session.room_conn, "UPLOAD_INIT", {
                        "room_name": room_name,
                        "filename": filename,
                        "filesize": filesize,
                        "chunk_size": chunk_size,
                        "total_chunks": total_chunks,
                        "checksum_sha256": checksum
                    })
                    
                    if not init_resp or init_resp.get("type") != "UPLOAD_READY":
                        self.send_response(conn, 400, {"error": f"Upload init failed: {init_resp.get('payload', {}).get('message') if init_resp else 'Timeout'}"})
                        return
                    
                    transfer_id = init_resp["payload"]["transfer_id"]
                    self.send_response(conn, 200, {"success": True, "transfer_id": transfer_id})
                    return

                elif action == "chunk":
                    transfer_id = int(qs.get("transfer_id", 0))
                    chunk_index = int(qs.get("chunk_index", 0))
                    
                    chunk_payload = {
                        "transfer_id": transfer_id,
                        "chunk_index": chunk_index,
                        "chunk_size": len(raw_body)
                    }
                    import struct
                    from common.protocol import build_packet
                    packet = build_packet("UPLOAD_CHUNK", chunk_payload, token=session.app.token)
                    packet["payload_size"] = len(raw_body)
                    header_json = json.dumps(packet).encode('utf-8')
                    
                    ev = threading.Event()
                    ack_res = {}
                    def cb(h):
                        ack_res["h"] = h
                        ev.set()
                    
                    with session.room_conn.lock:
                        session.room_conn.pending_requests[packet["request_id"]] = cb
                    
                    try:
                        with session.room_conn.write_lock:
                            session.room_conn.sock.sendall(struct.pack(">I", len(header_json)) + header_json + raw_body)
                    except Exception as e:
                        self.send_response(conn, 500, {"error": f"Socket send error: {e}"})
                        return
                    
                    if not ev.wait(30):
                        self.send_response(conn, 400, {"error": f"Chunk {chunk_index} timeout"})
                        return
                    
                    ack = ack_res.get("h")
                    if not ack or ack.get("type") != "CHUNK_ACK":
                        self.send_response(conn, 400, {"error": f"Chunk {chunk_index} failed"})
                        return
                        
                    self.send_response(conn, 200, {"success": True})
                    return

                elif action == "finish":
                    transfer_id = int(qs.get("transfer_id", 0))
                    finish_resp = self._sync_request(session.room_conn, "UPLOAD_FINISH", {"transfer_id": transfer_id})
                    if finish_resp and finish_resp.get("type") == "UPLOAD_SUCCESS":
                        self.send_response(conn, 200, {"success": True, "transfer_id": transfer_id})
                    else:
                        self.send_response(conn, 400, {"error": "Upload finish failed"})
                    return

                else:
                    self.send_response(conn, 400, {"error": f"Invalid action: {action}"})
                    return

            # 2. Legacy / Full-file Upload Flow (for backward compatibility if any)
            else:
                existing_transfer_id = qs.get("transfer_id")
                start_chunk = int(qs.get("start_chunk", 0))
                import hashlib
                filesize = len(raw_body) + (start_chunk * chunk_size if start_chunk > 0 else 0)
                total_chunks = (filesize + chunk_size - 1) // chunk_size
                
                sha256 = ""
                if start_chunk == 0:
                    sha256 = hashlib.sha256(raw_body).hexdigest()

                if not existing_transfer_id:
                    init_resp = self._sync_request(session.room_conn, "UPLOAD_INIT", {
                        "room_name": room_name,
                        "filename": filename,
                        "filesize": filesize,
                        "chunk_size": chunk_size,
                        "total_chunks": total_chunks,
                        "checksum_sha256": sha256
                    })

                    if not init_resp or init_resp.get("type") != "UPLOAD_READY":
                        self.send_response(conn, 400, {"error": f"Upload init failed: {init_resp.get('payload', {}).get('message') if init_resp else 'Timeout'}"})
                        return
                    transfer_id = init_resp["payload"]["transfer_id"]
                else:
                    transfer_id = int(existing_transfer_id)

                for i in range(len(raw_body) // chunk_size + (1 if len(raw_body) % chunk_size > 0 else 0)):
                    current_chunk_idx = start_chunk + i
                    chunk_data = raw_body[i*chunk_size : (i+1)*chunk_size]
                    chunk_payload = {
                        "transfer_id": transfer_id,
                        "chunk_index": current_chunk_idx,
                        "chunk_size": len(chunk_data)
                    }
                    import struct
                    from common.protocol import build_packet
                    packet = build_packet("UPLOAD_CHUNK", chunk_payload, token=session.app.token)
                    packet["payload_size"] = len(chunk_data)
                    header_json = json.dumps(packet).encode('utf-8')
                    
                    ev = threading.Event()
                    ack_res = {}
                    def cb(h):
                        ack_res["h"] = h
                        ev.set()
                    
                    with session.room_conn.lock:
                        session.room_conn.pending_requests[packet["request_id"]] = cb
                    
                    session.room_conn.sock.sendall(struct.pack(">I", len(header_json)) + header_json + chunk_data)
                    
                    if not ev.wait(10):
                        self.send_response(conn, 400, {"error": f"Chunk {current_chunk_idx} timeout"})
                        return
                    
                    ack = ack_res.get("h")
                    if not ack or ack.get("type") != "CHUNK_ACK":
                        self.send_response(conn, 400, {"error": f"Chunk {current_chunk_idx} failed"})
                        return

                finish_resp = self._sync_request(session.room_conn, "UPLOAD_FINISH", {"transfer_id": transfer_id})
                if finish_resp and finish_resp.get("type") == "UPLOAD_SUCCESS":
                    self.send_response(conn, 200, {"success": True, "transfer_id": transfer_id})
                else:
                    if finish_resp and finish_resp.get("type") == "ERROR":
                         self.send_response(conn, 200, {"success": True, "transfer_id": transfer_id, "status": "partial"})
                    else:
                         self.send_response(conn, 200, {"success": True, "transfer_id": transfer_id})
                return

        if path == "/api/rooms/files/download" and method == "GET":
            if not session.room_conn:
                self.send_response(conn, 400, {"error": "Not connected to room"})
                return
            file_id = qs.get("file_id")
            if not file_id:
                self.send_response(conn, 400, {"error": "Missing file_id"})
                return
                
            dl_resp = self._sync_request(session.room_conn, "DOWNLOAD_REQUEST", {"file_id": int(file_id)})
            if not dl_resp or dl_resp.get("type") != "DOWNLOAD_READY":
                self.send_response(conn, 400, {"error": "Download request failed"})
                return
                
            total_chunks = dl_resp["payload"]["total_chunks"]
            transfer_id = dl_resp["payload"]["transfer_id"]
            
            # Send HTTP chunked response headers
            response_headers = "HTTP/1.1 200 OK\r\n"
            response_headers += "Content-Type: application/octet-stream\r\n"
            response_headers += "Transfer-Encoding: chunked\r\n"
            response_headers += "Access-Control-Allow-Origin: *\r\n"
            response_headers += "Access-Control-Allow-Headers: Content-Type, Authorization, Session-Id\r\n"
            response_headers += "\r\n"
            
            try:
                conn.sendall(response_headers.encode('utf-8'))
            except Exception as e:
                print(f"Failed to send download headers: {e}")
                return
            
            ev = threading.Event()
            error_occurred = [False]
            
            class StreamDownloader:
                def __init__(self):
                    self.expected_chunk = 0
                
                def handle_chunk(self, idx, data):
                    if error_occurred[0]:
                        return
                    try:
                        # Write the chunk in HTTP chunked encoding format
                        chunk_header = f"{len(data):X}\r\n".encode('utf-8')
                        conn.sendall(chunk_header + data + b"\r\n")
                        self.expected_chunk += 1
                        if self.expected_chunk >= total_chunks:
                            ev.set()
                    except Exception as e:
                        print(f"Error streaming download chunk: {e}")
                        error_occurred[0] = True
                        ev.set()
            
            session.app.active_downloader = StreamDownloader()
            
            # Wait up to 5 minutes for streaming to complete
            ev.wait(300)
            session.app.active_downloader = None
            
            if not error_occurred[0]:
                try:
                    # Send ending chunk
                    conn.sendall(b"0\r\n\r\n")
                except:
                    pass
            return

        self.send_response(conn, 404, {"error": "Not Found"})

if __name__ == "__main__":
    server = WebServer(port=8080)
    server.start()
