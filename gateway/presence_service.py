import logging
from datetime import datetime
from common.db import get_db_connection

class PresenceService:
    def __init__(self):
        self.logger = logging.getLogger("PresenceService")

    def update_presence(self, user_id, username, status, server_id=None, active_room=None):
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with get_db_connection() as conn:
                cursor = conn.cursor()
                # Upsert presence
                cursor.execute("""
                    INSERT INTO user_presence (user_id, username, status, server_id, active_room, last_seen_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        username = excluded.username,
                        status = excluded.status,
                        server_id = excluded.server_id,
                        active_room = excluded.active_room,
                        last_seen_at = excluded.last_seen_at
                """, (user_id, username, status, server_id, active_room, now))
                conn.commit()
                return True
        except Exception as e:
            self.logger.exception(f"Error updating presence: {e}")
            return False

    def get_online_users(self):
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                # Only show users seen in the last 60 seconds
                cursor.execute("""
                    SELECT username, status, active_room
                    FROM user_presence
                    WHERE status != 'offline'
                    AND last_seen_at >= datetime('now', '-60 seconds', 'localtime')
                    ORDER BY username ASC
                """)
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            self.logger.exception(f"Error fetching online users: {e}")
            return []
