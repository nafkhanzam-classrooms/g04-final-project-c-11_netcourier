import hashlib
import os
import logging
from datetime import datetime
from common.db import get_db_connection

class AuthService:
    def __init__(self):
        self.logger = logging.getLogger("AuthService")
        self.salt_size = 16
        self.iterations = 100000

    def hash_password(self, password: str, salt: bytes = None) -> str:
        """Hash a password using PBKDF2-HMAC-SHA256."""
        if salt is None:
            salt = os.urandom(self.salt_size)
        
        pw_hash = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            self.iterations
        )
        # Store as salt:hash in hex
        return f"{salt.hex()}:{pw_hash.hex()}"

    def verify_password(self, password: str, stored_hash: str) -> bool:
        """Verify a password against a stored hash."""
        try:
            salt_hex, hash_hex = stored_hash.split(":")
            salt = bytes.fromhex(salt_hex)
            
            new_hash = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode("utf-8"),
                salt,
                self.iterations
            )
            return new_hash.hex() == hash_hex
        except Exception as e:
            self.logger.error(f"Password verification error: {e}")
            return False

    def register_user(self, username, password, display_name):
        """Register a new user in the database."""
        if not username or not password:
            return False, "MISSING_FIELD"
            
        password_hash = self.hash_password(password)
        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                # Check if username exists
                cursor.execute("SELECT user_id FROM users WHERE username = ?", (username,))
                if cursor.fetchone():
                    return False, "USERNAME_TAKEN"
                
                cursor.execute(
                    "INSERT INTO users (username, password_hash, display_name, created_at) VALUES (?, ?, ?, ?)",
                    (username, password_hash, display_name, created_at)
                )
                conn.commit()
                return True, None
        except Exception as e:
            self.logger.exception(f"Registration error: {e}")
            return False, "INTERNAL_ERROR"

    def authenticate(self, username, password):
        """Authenticate a user and return user info if successful."""
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT user_id, username, password_hash, display_name FROM users WHERE username = ?",
                    (username,)
                )
                row = cursor.fetchone()
                if row and self.verify_password(password, row["password_hash"]):
                    # Update last login
                    cursor.execute(
                        "UPDATE users SET last_login_at = ? WHERE user_id = ?",
                        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), row["user_id"])
                    )
                    conn.commit()
                    return {
                        "user_id": row["user_id"],
                        "username": row["username"],
                        "display_name": row["display_name"]
                    }
        except Exception as e:
            self.logger.exception(f"Authentication error: {e}")
        
        return None
