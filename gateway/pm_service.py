import logging
from datetime import datetime
from common.db import get_db_connection

class PMService:
    def __init__(self):
        self.logger = logging.getLogger("PMService")

    def get_user_id_by_username(self, username):
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT user_id FROM users WHERE username = ?", (username,))
                row = cursor.fetchone()
                return row["user_id"] if row else None
        except Exception as e:
            self.logger.exception(f"Error fetching user: {e}")
            return None

    def store_pm(self, sender_id, sender_username, recipient_id, recipient_username, content, status="sent"):
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            delivered_at = now if status == "delivered" else None
            
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO private_messages 
                    (sender_id, sender_username, recipient_id, recipient_username, content, status, created_at, delivered_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (sender_id, sender_username, recipient_id, recipient_username, content, status, now, delivered_at))
                conn.commit()
                return cursor.lastrowid
        except Exception as e:
            self.logger.exception(f"Error storing PM: {e}")
            return None

    def get_pm_history(self, user1_id, user2_id, limit=50):
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT sender_username, recipient_username, content, created_at, status
                    FROM private_messages
                    WHERE (sender_id = ? AND recipient_id = ?)
                       OR (sender_id = ? AND recipient_id = ?)
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (user1_id, user2_id, user2_id, user1_id, limit))
                # Return in chronological order
                rows = cursor.fetchall()
                return [dict(row) for row in reversed(rows)]
        except Exception as e:
            self.logger.exception(f"Error fetching PM history: {e}")
            return []
