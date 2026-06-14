import logging
from datetime import datetime
from common.db import get_db_connection

class RoomDirectoryService:
    def __init__(self, load_balancer, backend_service):
        self.logger = logging.getLogger("RoomDirectoryService")
        self.load_balancer = load_balancer
        self.backend_service = backend_service

    def get_room_list(self):
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                # Get rooms and their member counts (only users seen in last 60 seconds)
                cursor.execute("""
                    SELECT r.room_name as name, r.description, r.created_by as owner_id, COUNT(up.user_id) as members
                    FROM rooms r
                    LEFT JOIN user_presence up ON r.room_name = up.active_room 
                        AND up.status != 'offline'
                        AND up.last_seen_at >= datetime('now', '-60 seconds', 'localtime')
                    WHERE r.is_active = 1
                    GROUP BY r.room_id
                """)
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            self.logger.exception(f"Error fetching room list: {e}")
            return []

    def create_room(self, room_name, description, creator_id):
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                
                # Check if room already exists
                cursor.execute("SELECT room_id FROM rooms WHERE room_name = ?", (room_name,))
                if cursor.fetchone():
                    return False, "ROOM_ALREADY_EXISTS", None

                # Select best server
                server_id = self.load_balancer.select_backend()
                if not server_id:
                    return False, "BACKEND_DOWN", None

                # Create room
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                cursor.execute("""
                    INSERT INTO rooms (room_name, description, created_by, server_id, created_at, is_active)
                    VALUES (?, ?, ?, ?, ?, 1)
                """, (room_name, description, creator_id, server_id, now))
                room_id = cursor.lastrowid

                # Create mapping
                cursor.execute("""
                    INSERT INTO room_mapping (room_id, room_name, server_id, assigned_at)
                    VALUES (?, ?, ?, ?)
                """, (room_id, room_name, server_id, now))
                
                conn.commit()
                
                backend_info = self.backend_service.backends.get(server_id)
                return True, None, {
                    "room_id": room_id,
                    "room_name": room_name,
                    "server_id": server_id,
                    "host": backend_info["host"],
                    "port": backend_info["port"]
                }
        except Exception as e:
            self.logger.exception(f"Error creating room: {e}")
            return False, "INTERNAL_ERROR", None

    def join_room(self, room_name):
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                
                # Find room mapping
                cursor.execute("""
                    SELECT m.room_id, m.server_id, s.host, s.port, s.status
                    FROM room_mapping m
                    JOIN backend_servers s ON m.server_id = s.server_id
                    WHERE m.room_name = ? AND m.is_active = 1
                """, (room_name,))
                row = cursor.fetchone()
                
                if not row:
                    return False, "ROOM_NOT_FOUND", None
                
                if row["status"] != "alive":
                    return False, "BACKEND_DOWN", None
                
                return True, None, {
                    "room_id": row["room_id"],
                    "room_name": room_name,
                    "server_id": row["server_id"],
                    "host": row["host"],
                    "port": row["port"]
                }
        except Exception as e:
            self.logger.exception(f"Error joining room: {e}")
            return False, "INTERNAL_ERROR", None
