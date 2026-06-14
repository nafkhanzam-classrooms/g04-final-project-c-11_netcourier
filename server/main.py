import socket
import threading
import logging
import argparse
import time
import sys
import sqlite3
from datetime import datetime, timedelta

from common.constants import (
    DEFAULT_GATEWAY_HOST,
    DEFAULT_GATEWAY_BACKEND_PORT,
    PROJECT_ROOT
)
from common.protocol import receive_packet, send_packet, build_packet, build_error_packet
from common.logging_config import setup_logging
from common.db import get_db_connection

import os
import re

def sanitize_filename(filename):
    """Sanitize filename to prevent path traversal and other attacks."""
    # Keep only alphanumeric, dots, and underscores
    filename = re.sub(r'[^a-zA-Z0-9._-]', '_', filename)
    # Remove leading dots or slashes
    filename = filename.lstrip('./')
    return filename

class RateLimiter:
    def __init__(self, limit_seconds=0.5):
        self.limit_seconds = limit_seconds
        # user_id -> last_message_time
        self.last_action = {}
        self.lock = threading.Lock()

    def is_allowed(self, user_id):
        with self.lock:
            now = time.time()
            last = self.last_action.get(user_id, 0)
            if now - last < self.limit_seconds:
                return False
            self.last_action[user_id] = now
            return True

class ProcessServer:
    def __init__(self, server_id, host, port, gateway_host, gateway_port):
        self.server_id = server_id
        self.host = host
        self.port = port
        self.gateway_host = gateway_host
        self.gateway_port = gateway_port
        
        self.logger = setup_logging(f"server_{server_id.lower()}")
        
        # client_socket -> {user_id, username, current_room}
        self.clients = {}
        # room_name -> set(client_sockets)
        self.rooms = {}
        
        # Phase 9: Rate limiting
        self.chat_limiter = RateLimiter(limit_seconds=0.5)
        
        self.running = False
        self.gateway_sock = None
        self.lock = threading.Lock()
        self.transfer_progress = {}

    def start(self):
        self.running = True
        
        # 1. Connect to Gateway
        if not self._connect_to_gateway():
            self.logger.error("Failed to connect to Gateway. Exiting.")
            return

        # 2. Start heartbeat thread
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()
        
        # 3. Start client server
        client_thread = threading.Thread(target=self._run_client_server, daemon=True)
        client_thread.start()
        
        # Phase 9: Transfer timeout thread
        threading.Thread(target=self._transfer_timeout_loop, daemon=True).start()
        
        self.logger.info(f"Process Server {self.server_id} started on {self.host}:{self.port}")
        
        try:
            while self.running:
                threading.Event().wait(1)
        except KeyboardInterrupt:
            self.stop()

    def stop(self):
        self.logger.info(f"Stopping Process Server {self.server_id}...")
        self.running = False
        self._flush_all_dirty_transfers()
        if self.gateway_sock:
            self.gateway_sock.close()

    def _transfer_timeout_loop(self):
        while self.running:
            time.sleep(30) # Check every 30 seconds
            self._check_transfer_timeouts()

    def _check_transfer_timeouts(self, timeout_seconds=120): # 2 minutes
        try:
            with get_db_connection() as db_conn:
                cursor = db_conn.cursor()
                now = datetime.now()
                timeout_threshold = (now - timedelta(seconds=timeout_seconds)).strftime("%Y-%m-%d %H:%M:%S")
                
                # Mark stale transfers as failed
                cursor.execute("""
                    UPDATE file_transfers 
                    SET status = 'failed' 
                    WHERE status = 'in_progress' AND last_activity_at < ?
                """, (timeout_threshold,))
                
                if cursor.rowcount > 0:
                    self.logger.info(f"Marked {cursor.rowcount} stale transfers as failed.")
                db_conn.commit()
        except Exception as e:
            self.logger.error(f"Error checking transfer timeouts: {e}")

    def _flush_transfer_progress(self, transfer_id, db_conn=None):
        with self.lock:
            if transfer_id not in self.transfer_progress:
                return
            prog = self.transfer_progress[transfer_id]
            if not prog.get("dirty"):
                return
            completed_chunks = prog["completed_chunks"]
            bytes_transferred = prog["bytes_transferred"]
            last_activity = prog["last_activity_at"]
            
        try:
            if db_conn is None:
                conn_context = get_db_connection()
            else:
                class DummyContext:
                    def __init__(self, conn):
                        self.conn = conn
                    def __enter__(self):
                        return self.conn
                    def __exit__(self, exc_type, exc_val, exc_tb):
                        pass
                conn_context = DummyContext(db_conn)
                
            with conn_context as active_db:
                cursor = active_db.cursor()
                cursor.execute("""
                    UPDATE file_transfers 
                    SET completed_chunks = ?, 
                        bytes_transferred = ?,
                        last_activity_at = ?
                    WHERE transfer_id = ?
                """, (completed_chunks, bytes_transferred, last_activity, transfer_id))
                active_db.commit()
                
            with self.lock:
                if transfer_id in self.transfer_progress:
                    self.transfer_progress[transfer_id]["dirty"] = False
        except Exception as e:
            self.logger.error(f"Failed to flush transfer progress for {transfer_id}: {e}")

    def _flush_all_dirty_transfers(self):
        transfer_ids = list(self.transfer_progress.keys())
        for transfer_id in transfer_ids:
            self._flush_transfer_progress(transfer_id)
            with self.lock:
                prog = self.transfer_progress.get(transfer_id)
                if prog and "file_handle" in prog:
                    try:
                        prog["file_handle"].close()
                    except:
                        pass

    def _connect_to_gateway(self):
        try:
            self.gateway_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.gateway_sock.connect((self.gateway_host, self.gateway_port))
            self.gateway_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            
            # Register backend
            reg_packet = build_packet("REGISTER_BACKEND", {
                "server_id": self.server_id,
                "host": self.host,
                "port": self.port
            })
            send_packet(self.gateway_sock, reg_packet)
            
            header, _ = receive_packet(self.gateway_sock)
            if header.get("type") == "BACKEND_REGISTERED":
                self.logger.info(f"Successfully registered with Gateway as {self.server_id}")
                # Start gateway message listener (for VALIDATE_TOKEN responses etc)
                threading.Thread(target=self._listen_to_gateway, daemon=True).start()
                return True
            else:
                self.logger.error(f"Gateway rejected registration: {header}")
                return False
        except Exception as e:
            self.logger.exception(f"Error connecting to Gateway: {e}")
            return False

    def _listen_to_gateway(self):
        """Listen for unsolicited messages from Gateway if any, or just handle responses if needed."""
        # For now, most communication is request-response initiated by this server.
        # But we need to handle incoming packets if we use a shared socket for requests.
        # Actually, for VALIDATE_TOKEN, we'll send and wait for response.
        pass

    def _heartbeat_loop(self):
        while self.running:
            try:
                with self.lock:
                    active_rooms = len(self.rooms)
                    active_clients = len(self.clients)
                
                hb_packet = build_packet("HEARTBEAT", {
                    "server_id": self.server_id,
                    "stats": {
                        "active_rooms": active_rooms,
                        "active_clients": active_clients,
                        "active_transfers": 0 # Phase 7
                    }
                })
                send_packet(self.gateway_sock, hb_packet)
            except Exception as e:
                self.logger.error(f"Heartbeat failed: {e}")
                # Try to reconnect?
            
            time.sleep(5)

    def _run_client_server(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((self.host, self.port))
            s.listen()
            self.logger.info(f"Client server listening on {self.host}:{self.port}")
            
            while self.running:
                conn, addr = s.accept()
                try:
                    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                except Exception as e:
                    self.logger.warning(f"Failed to set TCP_NODELAY on client socket: {e}")
                threading.Thread(target=self._handle_client, args=(conn, addr), daemon=True).start()

    def _handle_client(self, conn, addr):
        self.logger.info(f"New client connection from {addr}")
        try:
            with conn:
                while self.running:
                    try:
                        header, binary_payload = receive_packet(conn)
                    except ValueError as e:
                        self.logger.warning(f"Malformed packet from {addr}: {e}")
                        send_packet(conn, build_error_packet("INVALID_PACKET", message=str(e)))
                        break

                    msg_type = header.get("type")
                    req_id = header.get("request_id")
                    token = header.get("token")
                    payload = header.get("payload", {})
                    
                    if msg_type == "AUTH_BACKEND":
                        self._handle_auth_backend(conn, req_id, token, payload)
                    elif msg_type == "JOIN_ROOM_BACKEND":
                        self._handle_join_room(conn, req_id, payload)
                    elif msg_type == "LEAVE_ROOM":
                        self._handle_leave_room(conn, req_id)
                    elif msg_type == "PING":
                        send_packet(conn, build_packet("PONG", request_id=req_id))
                    else:
                        # Ensure authenticated for other messages (Phase 6+)
                        if conn not in self.clients:
                            send_packet(conn, build_error_packet("INVALID_TOKEN", request_id=req_id))
                            continue
                        
                        user_id = self.clients[conn]["user_id"]
                        
                        if msg_type == "ROOM_CHAT_SEND":
                            if not self.chat_limiter.is_allowed(user_id):
                                send_packet(conn, build_error_packet("RATE_LIMIT_EXCEEDED", request_id=req_id))
                                continue
                            self._handle_room_chat_send(conn, req_id, payload)
                        elif msg_type == "ROOM_MESSAGE_REACTION":
                            self._handle_room_message_reaction(conn, req_id, payload)
                        elif msg_type == "ROOM_KICK_USER":
                            self._handle_room_kick_user(conn, req_id, payload)
                        elif msg_type == "ROOM_DELETE_FILE":
                            self._handle_room_delete_file(conn, req_id, payload)
                        elif msg_type == "ROOM_TYPING_INDICATOR":
                            self._handle_room_typing_indicator(conn, req_id, payload)
                        elif msg_type == "ROOM_MEMBER_LIST_REQUEST":
                            self._handle_room_member_list_request(conn, req_id, payload)
                        elif msg_type == "ROOM_HISTORY_REQUEST":
                            self._handle_room_history_request(conn, req_id, payload)
                        elif msg_type == "FILE_LIST_REQUEST":
                            self._handle_file_list_request(conn, req_id, payload)
                        elif msg_type == "UPLOAD_INIT":
                            self._handle_upload_init(conn, req_id, payload)
                        elif msg_type == "UPLOAD_CHUNK":
                            self._handle_upload_chunk(conn, req_id, payload, binary_payload)
                        elif msg_type == "UPLOAD_FINISH":
                            self._handle_upload_finish(conn, req_id, payload)
                        elif msg_type == "DOWNLOAD_REQUEST":
                            self._handle_download_request(conn, req_id, payload)
                        elif msg_type == "RESUME_TRANSFER":
                            self._handle_resume_transfer(conn, req_id, payload)
                        else:
                            self.logger.warning(f"Unhandled message type: {msg_type}")
        except (ConnectionError, socket.error):
            self.logger.info(f"Client {addr} disconnected")
        except Exception as e:
            self.logger.exception(f"Error handling client {addr}: {e}")
        finally:
            self._cleanup_client(conn)

    def _handle_auth_backend(self, conn, req_id, token, payload):
        if not token:
            send_packet(conn, build_error_packet("MISSING_FIELD", request_id=req_id))
            return

        # Validate token with Gateway
        # Note: Since we are using a single gateway_sock, we need to be careful with concurrent requests.
        # For simplicity in this phase, we use a lock or a fresh connection.
        # Let's use a fresh connection for validation to avoid multiplexing complexity on the control channel.
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as g_sock:
                g_sock.connect((self.gateway_host, self.gateway_port))
                val_packet = build_packet("VALIDATE_TOKEN", {"token": token})
                send_packet(g_sock, val_packet)
                
                header, _ = receive_packet(g_sock)
                if header.get("type") == "TOKEN_VALID":
                    res_payload = header.get("payload")
                    user_id = res_payload["user_id"]
                    username = res_payload["username"]
                    
                    self.logger.info(f"Token validation successful for user: {username}")
                    with self.lock:
                        self.clients[conn] = {
                            "user_id": user_id,
                            "username": username,
                            "current_room": None
                        }
                    
                    send_packet(conn, build_packet("AUTH_BACKEND_OK", {
                        "user_id": user_id,
                        "username": username
                    }, request_id=req_id))
                else:
                    send_packet(conn, build_error_packet("INVALID_TOKEN", request_id=req_id))
        except Exception as e:
            self.logger.error(f"Token validation failed: {e}")
            send_packet(conn, build_error_packet("INTERNAL_ERROR", request_id=req_id))

    def _handle_join_room(self, conn, req_id, payload):
        room_name = payload.get("room_name")
        if not room_name:
            send_packet(conn, build_error_packet("MISSING_FIELD", request_id=req_id))
            return
            
        if conn not in self.clients:
            send_packet(conn, build_error_packet("INVALID_TOKEN", request_id=req_id))
            return

        user_info = self.clients[conn]
        
        with self.lock:
            # Leave previous room if any
            if user_info["current_room"]:
                self._leave_room_logic(conn)
            
            # Join new room
            if room_name not in self.rooms:
                self.rooms[room_name] = set()
            self.rooms[room_name].add(conn)
            user_info["current_room"] = room_name
            
        # Update presence via Gateway
        self._update_presence_gateway(user_info["user_id"], user_info["username"], "in_room", room_name)
        
        # Add members list to system event
        members = self._get_room_members(room_name)
        
        # Broadcast join message
        self._broadcast_system_event(room_name, f"User {user_info['username']} joined the room.", members)
        
        # In Phase 6, we would send history here
        # Actually client requests history via ROOM_HISTORY_REQUEST after JOIN_ROOM_OK
        send_packet(conn, build_packet("JOIN_ROOM_OK", {"room_name": room_name}, request_id=req_id))
        self.logger.info(f"User {user_info['username']} joined room {room_name}")

    def _handle_leave_room(self, conn, req_id):
        if conn not in self.clients:
            send_packet(conn, build_error_packet("INVALID_TOKEN", request_id=req_id))
            return

        user_info = self.clients[conn]
        room_name = user_info["current_room"]
        
        if room_name:
            self._leave_room_logic(conn)
            self._update_presence_gateway(user_info["user_id"], user_info["username"], "waiting")
            send_packet(conn, build_packet("LEAVE_ROOM_OK", {"room_name": room_name}, request_id=req_id))
            self.logger.info(f"User {user_info['username']} left room {room_name}")
            
            members = self._get_room_members(room_name)
            self._broadcast_system_event(room_name, f"User {user_info['username']} left the room.", members)
        else:
            send_packet(conn, build_error_packet("NOT_IN_ROOM", request_id=req_id))

    def _leave_room_logic(self, conn):
        user_info = self.clients[conn]
        room_name = user_info["current_room"]
        if room_name and room_name in self.rooms:
            self.rooms[room_name].discard(conn)
            if not self.rooms[room_name]:
                del self.rooms[room_name]
            user_info["current_room"] = None

    def _get_room_members(self, room_name):
        with self.lock:
            if room_name not in self.rooms:
                return []
            return [self.clients[c]["username"] for c in self.rooms[room_name]]

    def _broadcast_system_event(self, room_name, message, members=None):
        if not members:
            members = self._get_room_members(room_name)
            
        room_id = self._get_room_id(room_name)
        if room_id:
            try:
                with get_db_connection() as db_conn:
                    cursor = db_conn.cursor()
                    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    cursor.execute("""
                        INSERT INTO room_messages (room_id, server_id, sender_username, message_type, content, created_at)
                        VALUES (?, ?, 'System', 'system', ?, ?)
                    """, (room_id, self.server_id, message, now))
                    db_conn.commit()
            except Exception as e:
                self.logger.error(f"Failed to save system event: {e}")
                
        packet = build_packet("SYSTEM_EVENT", {
            "room_name": room_name,
            "event_type": "room_event",
            "message": message,
            "members": members
        })
        self._broadcast_to_room(room_name, packet)

    def _broadcast_to_room(self, room_name, packet):
        with self.lock:
            if room_name not in self.rooms:
                return
            for c in self.rooms[room_name]:
                try:
                    send_packet(c, packet)
                except Exception as e:
                    self.logger.error(f"Error broadcasting to client in {room_name}: {e}")

    def _get_room_id(self, room_name):
        with get_db_connection() as db_conn:
            cursor = db_conn.cursor()
            cursor.execute("SELECT room_id FROM rooms WHERE room_name = ?", (room_name,))
            row = cursor.fetchone()
            if row:
                return row["room_id"]
        return None

    def _handle_room_chat_send(self, conn, req_id, payload):
        room_name = payload.get("room_name")
        message = payload.get("message")
        
        if not room_name or not message:
            send_packet(conn, build_error_packet("MISSING_FIELD", request_id=req_id))
            return
            
        user_info = self.clients[conn]
        if user_info["current_room"] != room_name:
            send_packet(conn, build_error_packet("NOT_IN_ROOM", request_id=req_id))
            return

        room_id = self._get_room_id(room_name)
        if not room_id:
            send_packet(conn, build_error_packet("ROOM_NOT_FOUND", request_id=req_id))
            return

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Save to DB
        try:
            with get_db_connection() as db_conn:
                cursor = db_conn.cursor()
                cursor.execute("""
                    INSERT INTO room_messages (room_id, server_id, sender_id, sender_username, message_type, content, created_at)
                    VALUES (?, ?, ?, ?, 'text', ?, ?)
                """, (room_id, self.server_id, user_info["user_id"], user_info["username"], message, now))
                message_id = cursor.lastrowid
                db_conn.commit()
                self.logger.info(f"DEBUG: Generated message_id: {message_id}")
        except Exception as e:
            self.logger.error(f"Failed to save room message: {e}")
            send_packet(conn, build_error_packet("INTERNAL_ERROR", request_id=req_id))
            return

        # Broadcast
        broadcast_packet = build_packet("ROOM_CHAT_BROADCAST", {
            "message_id": message_id,
            "room_id": room_id,
            "room_name": room_name,
            "sender_username": user_info["username"],
            "message": message,
            "timestamp": now,
            "reactions": {}
        }, request_id=req_id)
        
        self._broadcast_to_room(room_name, broadcast_packet)

    def _handle_room_message_reaction(self, conn, req_id, payload):
        message_id = payload.get("message_id")
        emoji = payload.get("emoji")
        action = payload.get("action", "add") # 'add' or 'remove'
        
        user_info = self.clients[conn]
        room_name = user_info["current_room"]
        
        if not all([message_id, emoji, room_name]):
            send_packet(conn, build_error_packet("MISSING_FIELD", request_id=req_id))
            return

        try:
            message_id = int(message_id)
            with get_db_connection() as db_conn:
                cursor = db_conn.cursor()
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                if action == "add":
                    try:
                        cursor.execute("""
                            INSERT INTO message_reactions (message_id, user_id, username, emoji, created_at)
                            VALUES (?, ?, ?, ?, ?)
                        """, (message_id, user_info["user_id"], user_info["username"], emoji, now))
                    except sqlite3.IntegrityError:
                        pass # Already exists
                else:
                    cursor.execute("""
                        DELETE FROM message_reactions 
                        WHERE message_id = ? AND user_id = ? AND emoji = ?
                    """, (message_id, user_info["user_id"], emoji))
                
                db_conn.commit()
                
                # Get all reactions for this message to broadcast the updated state
                cursor.execute("""
                    SELECT emoji, COUNT(*) as count, GROUP_CONCAT(username) as usernames
                    FROM message_reactions
                    WHERE message_id = ?
                    GROUP BY emoji
                """, (message_id,))
                
                all_reactions = {}
                for row in cursor.fetchall():
                    all_reactions[row["emoji"]] = {
                        "count": row["count"],
                        "usernames": row["usernames"].split(",") if row["usernames"] else []
                    }
                
                broadcast_packet = build_packet("ROOM_REACTION_BROADCAST", {
                    "message_id": message_id,
                    "room_name": room_name,
                    "emoji": emoji,
                    "action": action,
                    "username": user_info["username"],
                    "reactions": all_reactions
                }, request_id=req_id)
                
                self._broadcast_to_room(room_name, broadcast_packet)
        except Exception as e:
            self.logger.error(f"Failed to handle reaction ({action}): {e}")
            send_packet(conn, build_error_packet("INTERNAL_ERROR", request_id=req_id))

    def _is_room_owner(self, user_id, room_name):
        try:
            with get_db_connection() as db_conn:
                cursor = db_conn.cursor()
                cursor.execute("SELECT created_by FROM rooms WHERE room_name = ?", (room_name,))
                row = cursor.fetchone()
                if row and row["created_by"] == user_id:
                    return True
        except Exception as e:
            self.logger.error(f"Error checking room owner: {e}")
        return False

    def _handle_room_kick_user(self, conn, req_id, payload):
        target_username = payload.get("username")
        user_info = self.clients[conn]
        room_name = user_info["current_room"]
        
        if not self._is_room_owner(user_info["user_id"], room_name):
            send_packet(conn, build_error_packet("PERMISSION_DENIED", request_id=req_id))
            return

        # Find target connection
        target_conn = None
        with self.lock:
            for c, info in self.clients.items():
                if info["username"] == target_username and info["current_room"] == room_name:
                    target_conn = c
                    break
        
        if target_conn:
            send_packet(target_conn, build_packet("SYSTEM_EVENT", {
                "room_name": room_name,
                "message": "You have been kicked from the room by the owner.",
                "event_type": "kicked"
            }))
            self._cleanup_client(target_conn)
            try:
                target_conn.close()
            except:
                pass
            
            self._broadcast_system_event(room_name, f"User {target_username} has been kicked by the owner.")
            send_packet(conn, build_packet("KICK_USER_OK", {"username": target_username}, request_id=req_id))
        else:
            send_packet(conn, build_error_packet("USER_NOT_FOUND", request_id=req_id))

    def _handle_room_delete_file(self, conn, req_id, payload):
        file_id = payload.get("file_id")
        user_info = self.clients[conn]
        room_name = user_info["current_room"]
        
        if not self._is_room_owner(user_info["user_id"], room_name):
            send_packet(conn, build_error_packet("PERMISSION_DENIED", request_id=req_id))
            return

        try:
            message_id = None
            with get_db_connection() as db_conn:
                cursor = db_conn.cursor()
                # Get the message_id of the file message
                cursor.execute("SELECT message_id FROM room_messages WHERE message_type = 'file' AND content LIKE ? AND is_deleted = 0", (f'%\"file_id\": {file_id}%',))
                row = cursor.fetchone()
                if row:
                    message_id = row["message_id"]

                # Logical delete from files table
                cursor.execute("UPDATE files SET status = 'deleted' WHERE file_id = ?", (file_id,))
                # Logical delete from room_messages (the file card)
                cursor.execute("UPDATE room_messages SET is_deleted = 1 WHERE message_type = 'file' AND content LIKE ?", (f'%\"file_id\": {file_id}%',))
                db_conn.commit()
                
            self._broadcast_system_event(room_name, f"A file was deleted by the owner.")
            
            if message_id:
                broadcast_packet = build_packet("ROOM_DELETE_FILE_BROADCAST", {
                    "room_name": room_name,
                    "message_id": message_id,
                    "file_id": file_id
                })
                self._broadcast_to_room(room_name, broadcast_packet)
                
            send_packet(conn, build_packet("DELETE_FILE_OK", {"file_id": file_id}, request_id=req_id))
        except Exception as e:
            self.logger.error(f"Failed to delete file: {e}")
            send_packet(conn, build_error_packet("INTERNAL_ERROR", request_id=req_id))

    def _handle_room_typing_indicator(self, conn, req_id, payload):
        is_typing = payload.get("is_typing", False)
        user_info = self.clients[conn]
        room_name = user_info["current_room"]
        
        if not room_name:
            return

        broadcast_packet = build_packet("ROOM_TYPING_BROADCAST", {
            "room_name": room_name,
            "username": user_info["username"],
            "is_typing": is_typing
        }, request_id=req_id)
        
        # Don't send back to the sender
        with self.lock:
            if room_name in self.rooms:
                for c in self.rooms[room_name]:
                    if c != conn:
                        try:
                            send_packet(c, broadcast_packet)
                        except:
                            pass

    def _handle_room_member_list_request(self, conn, req_id, payload):
        room_name = payload.get("room_name")
        user_info = self.clients[conn]
        if user_info["current_room"] != room_name:
            send_packet(conn, build_error_packet("NOT_IN_ROOM", request_id=req_id))
            return

        members = self._get_room_members(room_name)
        send_packet(conn, build_packet("ROOM_MEMBER_LIST_RESPONSE", {
            "room_name": room_name,
            "members": members
        }, request_id=req_id))

    def _handle_room_history_request(self, conn, req_id, payload):
        room_name = payload.get("room_name")
        limit = payload.get("limit", 50)
        
        user_info = self.clients[conn]
        if user_info["current_room"] != room_name:
            send_packet(conn, build_error_packet("NOT_IN_ROOM", request_id=req_id))
            return

        room_id = self._get_room_id(room_name)
        if not room_id:
            send_packet(conn, build_error_packet("ROOM_NOT_FOUND", request_id=req_id))
            return

        try:
            with get_db_connection() as db_conn:
                cursor = db_conn.cursor()
                cursor.execute("""
                    SELECT message_id, sender_username, content, created_at, message_type
                    FROM room_messages
                    WHERE room_id = ? AND is_deleted = 0
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (room_id, limit))
                
                rows = cursor.fetchall()
                messages = []
                for row in reversed(rows): # Reverse to chronological
                    msg_id = row["message_id"]
                    # Fetch reactions for this message
                    cursor.execute("""
                        SELECT emoji, COUNT(*) as count, GROUP_CONCAT(username) as usernames
                        FROM message_reactions
                        WHERE message_id = ?
                        GROUP BY emoji
                    """, (msg_id,))
                    
                    reactions = {}
                    for r_row in cursor.fetchall():
                        reactions[r_row["emoji"]] = {
                            "count": r_row["count"],
                            "usernames": r_row["usernames"].split(",") if r_row["usernames"] else []
                        }

                    messages.append({
                        "message_id": msg_id,
                        "sender_username": row["sender_username"],
                        "message": row["content"],
                        "timestamp": row["created_at"],
                        "message_type": row["message_type"],
                        "reactions": reactions
                    })
                
                response = build_packet("ROOM_HISTORY_RESPONSE", {
                    "room_name": room_name,
                    "messages": messages
                }, request_id=req_id)
                send_packet(conn, response)
        except Exception as e:
            self.logger.error(f"Failed to fetch room history: {e}")
            send_packet(conn, build_error_packet("INTERNAL_ERROR", request_id=req_id))

    def _update_presence_gateway(self, user_id, username, status, room_name=None):
        """Notify Gateway about user presence change."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as g_sock:
                g_sock.connect((self.gateway_host, self.gateway_port))
                packet = build_packet("USER_ROOM_STATUS_UPDATE", {
                    "user_id": user_id,
                    "username": username,
                    "status": status,
                    "server_id": self.server_id,
                    "room_name": room_name
                })
                send_packet(g_sock, packet)
                # No need to wait for response for fire-and-forget notification
        except Exception as e:
            self.logger.error(f"Failed to update presence to Gateway: {e}")

    def _handle_file_list_request(self, conn, req_id, payload):
        room_name = payload.get("room_name")
        if not room_name:
            send_packet(conn, build_error_packet("MISSING_FIELD", request_id=req_id))
            return
            
        room_id = self._get_room_id(room_name)
        if not room_id:
            send_packet(conn, build_error_packet("ROOM_NOT_FOUND", request_id=req_id))
            return
            
        try:
            with get_db_connection() as db_conn:
                cursor = db_conn.cursor()
                cursor.execute("""
                    SELECT f.file_id, f.original_filename, f.size_bytes, u.username as uploader_username
                    FROM files f
                    JOIN users u ON f.uploader_id = u.user_id
                    WHERE f.room_id = ? AND f.status = 'available'
                """, (room_id,))
                
                files = [dict(row) for row in cursor.fetchall()]
                
                response = build_packet("FILE_LIST_RESPONSE", {
                    "room_name": room_name,
                    "files": files
                }, request_id=req_id)
                send_packet(conn, response)
        except Exception as e:
            self.logger.error(f"Failed to fetch file list: {e}")
            send_packet(conn, build_error_packet("INTERNAL_ERROR", request_id=req_id))

    def _handle_upload_init(self, conn, req_id, payload):
        room_id = payload.get("room_id")
        room_name = payload.get("room_name")
        filename = payload.get("filename")
        filesize = payload.get("filesize")
        chunk_size = payload.get("chunk_size")
        total_chunks = payload.get("total_chunks")
        checksum = payload.get("checksum_sha256")
        
        user_info = self.clients[conn]
        
        if not room_id and room_name:
            room_id = self._get_room_id(room_name)
            
        if not all([room_id, filename, filesize, chunk_size, total_chunks, checksum]):
            send_packet(conn, build_error_packet("MISSING_FIELD", request_id=req_id))
            return

        # Phase 9: File size limit (e.g., 1024MB)
        if filesize > 1024 * 1024 * 1024:
            send_packet(conn, build_error_packet("FILE_TOO_LARGE", message="Max file size is 1024MB", request_id=req_id))
            return

        # Phase 9: Filename sanitization
        filename = sanitize_filename(filename)
            
        try:
            with get_db_connection() as db_conn:
                cursor = db_conn.cursor()
                from datetime import datetime, timedelta
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                import os
                import hashlib
                from common.constants import PROJECT_ROOT
                import uuid
                storage_dir = os.path.join(PROJECT_ROOT, "storage", self.server_id, str(room_id))
                os.makedirs(storage_dir, exist_ok=True)
                
                # Use UUID for disk filename to prevent collisions (Phase 10 fix)
                unique_id = str(uuid.uuid4())
                stored_filename = f"{unique_id}_{filename}"
                stored_path = os.path.join(storage_dir, stored_filename)
                
                # Touch the file to ensure it exists for seek/out-of-order writes
                with open(stored_path, "wb") as f:
                    pass
                
                cursor.execute("""
                    INSERT INTO files (room_id, server_id, uploader_id, original_filename, stored_filename, stored_path, size_bytes, checksum_sha256, chunk_size, total_chunks, status, uploaded_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'uploading', ?)
                """, (room_id, self.server_id, user_info["user_id"], filename, stored_filename, stored_path, filesize, checksum, chunk_size, total_chunks, now))
                file_id = cursor.lastrowid
                
                cursor.execute("""
                    INSERT INTO file_transfers (file_id, room_id, server_id, user_id, direction, status, total_chunks, started_at, last_activity_at)
                    VALUES (?, ?, ?, ?, 'upload', 'in_progress', ?, ?, ?)
                """, (file_id, room_id, self.server_id, user_info["user_id"], total_chunks, now, now))
                transfer_id = cursor.lastrowid
                db_conn.commit()
                
                send_packet(conn, build_packet("UPLOAD_READY", {
                    "transfer_id": transfer_id,
                    "start_chunk": 0
                }, request_id=req_id))
        except Exception as e:
            self.logger.error(f"Upload init failed: {e}")
            send_packet(conn, build_error_packet("INTERNAL_ERROR", request_id=req_id))

    def _handle_upload_chunk(self, conn, req_id, payload, binary_payload):
        transfer_id = payload.get("transfer_id")
        chunk_index = payload.get("chunk_index")
        
        try:
            with self.lock:
                if transfer_id not in self.transfer_progress:
                    with get_db_connection() as db_conn:
                        cursor = db_conn.cursor()
                        cursor.execute("""
                            SELECT f.stored_path, f.chunk_size, ft.status, ft.total_chunks, ft.completed_chunks, ft.bytes_transferred
                            FROM file_transfers ft
                            JOIN files f ON ft.file_id = f.file_id
                            WHERE ft.transfer_id = ?
                        """, (transfer_id,))
                        row = cursor.fetchone()
                        if not row or row["status"] != "in_progress":
                            send_packet(conn, build_error_packet("INVALID_PACKET", request_id=req_id))
                            return
                        
                        self.transfer_progress[transfer_id] = {
                            "stored_path": row["stored_path"],
                            "chunk_size": row["chunk_size"],
                            "total_chunks": row["total_chunks"],
                            "completed_chunks": row["completed_chunks"],
                            "bytes_transferred": row["bytes_transferred"],
                            "dirty": False,
                            "last_activity_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        }
                
                prog = self.transfer_progress[transfer_id]
                stored_path = prog["stored_path"]
                chunk_size = prog["chunk_size"]
                total_chunks = prog["total_chunks"]
                
            if not os.path.exists(stored_path):
                with open(stored_path, "wb") as f:
                    pass
                
            offset = chunk_index * chunk_size
            with self.lock:
                prog = self.transfer_progress[transfer_id]
                if "file_handle" not in prog or prog["file_handle"].closed:
                    prog["file_handle"] = open(stored_path, "r+b")
                f = prog["file_handle"]
                f.seek(offset)
                f.write(binary_payload)
                
            with self.lock:
                prog = self.transfer_progress[transfer_id]
                prog["completed_chunks"] += 1
                prog["bytes_transferred"] += len(binary_payload)
                prog["last_activity_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                prog["dirty"] = True
                
                completed_chunks = prog["completed_chunks"]
                should_flush = (chunk_index == 0 or completed_chunks % 20 == 0 or chunk_index + 1 == total_chunks)
                
            if should_flush:
                self._flush_transfer_progress(transfer_id)
                
            send_packet(conn, build_packet("CHUNK_ACK", {
                "transfer_id": transfer_id,
                "chunk_index": chunk_index,
                "status": "received"
            }, request_id=req_id))
        except Exception as e:
            self.logger.error(f"Upload chunk failed: {e}")
            send_packet(conn, build_error_packet("INTERNAL_ERROR", request_id=req_id))

    def _handle_upload_finish(self, conn, req_id, payload):
        transfer_id = payload.get("transfer_id")
        
        # Close open file handle if any
        with self.lock:
            prog = self.transfer_progress.get(transfer_id)
            if prog and "file_handle" in prog:
                try:
                    prog["file_handle"].close()
                except:
                    pass
        
        # Flush transfer progress to DB first
        self._flush_transfer_progress(transfer_id)
        
        try:
            with get_db_connection() as db_conn:
                cursor = db_conn.cursor()
                cursor.execute("""
                    SELECT f.file_id, f.stored_path, f.checksum_sha256, f.original_filename, f.room_id
                    FROM file_transfers ft
                    JOIN files f ON ft.file_id = f.file_id
                    WHERE ft.transfer_id = ?
                """, (transfer_id,))
                row = cursor.fetchone()
                
                if row:
                    import hashlib
                    sha256 = hashlib.sha256()
                    with open(row["stored_path"], "rb") as f:
                        for chunk in iter(lambda: f.read(65536), b""):
                            sha256.update(chunk)
                    calc_checksum = sha256.hexdigest()
                    
                    if calc_checksum == row["checksum_sha256"]:
                        cursor.execute("UPDATE files SET status = 'available' WHERE file_id = ?", (row["file_id"],))
                        cursor.execute("UPDATE file_transfers SET status = 'completed' WHERE transfer_id = ?", (transfer_id,))
                        db_conn.commit()
                        
                        # Clean up cache
                        with self.lock:
                            if transfer_id in self.transfer_progress:
                                del self.transfer_progress[transfer_id]
                        
                        send_packet(conn, build_packet("UPLOAD_SUCCESS", {
                            "transfer_id": transfer_id
                        }, request_id=req_id))
                        
                        # Broadcast system event and file message
                        user_info = self.clients[conn]
                        cursor.execute("SELECT room_name FROM rooms WHERE room_id = ?", (row["room_id"],))
                        room_row = cursor.fetchone()
                        if room_row:
                            room_name = room_row["room_name"]
                            self._broadcast_system_event(room_name, f"User {user_info['username']} uploaded {row['original_filename']}")
                            
                            # Also broadcast as a file chat message
                            import json
                            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            
                            # Re-fetch size_bytes to be safe
                            cursor.execute("SELECT size_bytes FROM files WHERE file_id = ?", (row["file_id"],))
                            size_row = cursor.fetchone()
                            size_bytes = size_row["size_bytes"] if size_row else 0
                            
                            file_msg_content = json.dumps({
                                "file_id": row["file_id"],
                                "filename": row["original_filename"],
                                "size_bytes": size_bytes
                            })
                            
                            cursor.execute("""
                                INSERT INTO room_messages (room_id, server_id, sender_id, sender_username, message_type, content, created_at)
                                VALUES (?, ?, ?, ?, 'file', ?, ?)
                            """, (row["room_id"], self.server_id, user_info["user_id"], user_info["username"], file_msg_content, now))
                            db_conn.commit()
                            
                            broadcast_packet = build_packet("ROOM_CHAT_BROADCAST", {
                                "room_id": row["room_id"],
                                "room_name": room_name,
                                "sender_username": user_info["username"],
                                "message": file_msg_content,
                                "timestamp": now,
                                "message_type": "file"
                            }, request_id=req_id)
                            self._broadcast_to_room(room_name, broadcast_packet)
                    else:
                        send_packet(conn, build_error_packet("CHECKSUM_FAILED", request_id=req_id))
        except Exception as e:
            self.logger.error(f"Upload finish failed: {e}")
            send_packet(conn, build_error_packet("INTERNAL_ERROR", request_id=req_id))

    def _handle_download_request(self, conn, req_id, payload):
        file_id = payload.get("file_id")
        user_info = self.clients[conn]
        
        try:
            with get_db_connection() as db_conn:
                cursor = db_conn.cursor()
                cursor.execute("SELECT * FROM files WHERE file_id = ?", (file_id,))
                file_row = cursor.fetchone()
                
                if not file_row or file_row["status"] != "available":
                    send_packet(conn, build_error_packet("FILE_NOT_FOUND", request_id=req_id))
                    return
                
                from datetime import datetime, timedelta
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                cursor.execute("""
                    INSERT INTO file_transfers (file_id, room_id, server_id, user_id, direction, status, total_chunks, started_at, last_activity_at)
                    VALUES (?, ?, ?, ?, 'download', 'in_progress', ?, ?, ?)
                """, (file_id, file_row["room_id"], self.server_id, user_info["user_id"], file_row["total_chunks"], now, now))
                transfer_id = cursor.lastrowid
                db_conn.commit()
                
                send_packet(conn, build_packet("DOWNLOAD_READY", {
                    "transfer_id": transfer_id,
                    "total_chunks": file_row["total_chunks"],
                    "chunk_size": file_row["chunk_size"],
                    "checksum_sha256": file_row["checksum_sha256"]
                }, request_id=req_id))
                
                # Start pushing chunks in a thread to avoid blocking client loop
                import threading
                threading.Thread(target=self._push_download_chunks, args=(conn, transfer_id, file_row), daemon=True).start()
        except Exception as e:
            self.logger.error(f"Download request failed: {e}")
            send_packet(conn, build_error_packet("INTERNAL_ERROR", request_id=req_id))

    def _handle_resume_transfer(self, conn, req_id, payload):
        transfer_id = payload.get("transfer_id")
        direction = payload.get("direction")
        
        # Flush transfer progress for this transfer_id to DB first
        self._flush_transfer_progress(transfer_id)
        
        user_info = self.clients.get(conn)
        if not user_info:
            send_packet(conn, build_error_packet("INVALID_TOKEN", request_id=req_id))
            return
            
        try:
            with get_db_connection() as db_conn:
                cursor = db_conn.cursor()
                cursor.execute("""
                    SELECT ft.*, f.stored_path, f.chunk_size, f.checksum_sha256
                    FROM file_transfers ft
                    JOIN files f ON ft.file_id = f.file_id
                    WHERE ft.transfer_id = ? AND ft.user_id = ?
                """, (transfer_id, user_info["user_id"]))
                
                row = cursor.fetchone()
                if not row or row["status"] != "in_progress":
                    send_packet(conn, build_error_packet("INVALID_PACKET", request_id=req_id))
                    return
                    
                if direction == "upload":
                    completed_chunks = row["completed_chunks"]
                    send_packet(conn, build_packet("UPLOAD_READY", {
                        "transfer_id": transfer_id,
                        "start_chunk": completed_chunks
                    }, request_id=req_id))
                    
                elif direction == "download":
                    start_chunk = payload.get("start_chunk", 0)
                    send_packet(conn, build_packet("DOWNLOAD_READY", {
                        "transfer_id": transfer_id,
                        "total_chunks": row["total_chunks"],
                        "chunk_size": row["chunk_size"],
                        "checksum_sha256": row["checksum_sha256"],
                        "start_chunk": start_chunk
                    }, request_id=req_id))
                    
                    import threading
                    threading.Thread(target=self._push_download_chunks, args=(conn, transfer_id, row, start_chunk), daemon=True).start()
                    
        except Exception as e:
            self.logger.error(f"Resume transfer failed: {e}")
            send_packet(conn, build_error_packet("INTERNAL_ERROR", request_id=req_id))

    def _push_download_chunks(self, conn, transfer_id, file_row, start_chunk=0):
        stored_path = file_row["stored_path"]
        chunk_size = file_row["chunk_size"]
        total_chunks = file_row["total_chunks"]
        
        try:
            with open(stored_path, "rb") as f:
                if start_chunk > 0:
                    f.seek(start_chunk * chunk_size)
                for i in range(start_chunk, total_chunks):
                    chunk_data = f.read(chunk_size)
                    import json
                    import struct
                    packet = build_packet("DOWNLOAD_CHUNK", {
                        "transfer_id": transfer_id,
                        "chunk_index": i
                    })
                    packet["payload_size"] = len(chunk_data)
                    header_json = json.dumps(packet).encode('utf-8')
                    try:
                        conn.sendall(struct.pack(">I", len(header_json)) + header_json + chunk_data)
                    except Exception as e:
                        self.logger.error(f"Error sending download chunk {i}: {e}")
                        break
        except Exception as e:
            self.logger.error(f"Failed to push download chunks: {e}")

    def _cleanup_client(self, conn):
        self._flush_all_dirty_transfers()
        room_to_broadcast = None
        username_left = None
        
        with self.lock:
            if conn in self.clients:
                user_info = self.clients[conn]
                if user_info["current_room"]:
                    room_to_broadcast = user_info["current_room"]
                    username_left = user_info["username"]
                    self._leave_room_logic(conn)
                    self._update_presence_gateway(user_info["user_id"], user_info["username"], "offline")
                del self.clients[conn]
                
        if room_to_broadcast and username_left:
            self._broadcast_system_event(room_to_broadcast, f"User {username_left} left the room.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NetCourier Process Server")
    parser.add_argument("--server-id", required=True, help="S1, S2, etc.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--gateway-host", default=DEFAULT_GATEWAY_HOST)
    parser.add_argument("--gateway-port", type=int, default=DEFAULT_GATEWAY_BACKEND_PORT)
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    
    args = parser.parse_args()
    
    # The setup_logging function already handles component name, 
    # but we can adjust the root logger or pass debug level if we want.
    # For simplicity, if --debug is set, let's ensure the level is DEBUG.
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    
    server = ProcessServer(args.server_id, args.host, args.port, args.gateway_host, args.gateway_port)
    server.start()
