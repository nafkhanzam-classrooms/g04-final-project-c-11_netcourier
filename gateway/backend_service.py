import logging
import threading
from datetime import datetime, timedelta
from common.db import get_db_connection

class BackendService:
    def __init__(self, timeout_seconds=15):
        self.logger = logging.getLogger("BackendService")
        self.timeout_seconds = timeout_seconds
        
        # server_id -> {host, port, status, last_heartbeat, socket, active_rooms, active_clients, active_transfers}
        self.backends = {}
        self.lock = threading.Lock()
        
        # Start health check thread
        self.running = True
        self.health_check_thread = threading.Thread(target=self._health_check_loop, daemon=True)
        self.health_check_thread.start()

    def register_backend(self, server_id, host, port, conn):
        with self.lock:
            self.backends[server_id] = {
                "host": host,
                "port": port,
                "status": "alive",
                "last_heartbeat": datetime.now(),
                "socket": conn,
                "active_rooms": 0,
                "active_clients": 0,
                "active_transfers": 0
            }
            
            # Sync to database
            try:
                with get_db_connection() as db_conn:
                    cursor = db_conn.cursor()
                    cursor.execute("""
                        INSERT INTO backend_servers (server_id, host, port, status, last_heartbeat_at)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(server_id) DO UPDATE SET
                            host = excluded.host,
                            port = excluded.port,
                            status = excluded.status,
                            last_heartbeat_at = excluded.last_heartbeat_at
                    """, (server_id, host, port, "alive", datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                    db_conn.commit()
            except Exception as e:
                self.logger.exception(f"Error syncing backend {server_id} to DB: {e}")
                
        self.logger.info(f"Backend {server_id} registered at {host}:{port}")

    def update_heartbeat(self, server_id, stats=None):
        with self.lock:
            if server_id in self.backends:
                self.backends[server_id]["last_heartbeat"] = datetime.now()
                self.backends[server_id]["status"] = "alive"
                if stats:
                    self.backends[server_id].update(stats)
                
                # Sync to database
                try:
                    with get_db_connection() as db_conn:
                        cursor = db_conn.cursor()
                        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        
                        if stats:
                            cursor.execute("""
                                UPDATE backend_servers 
                                SET status = 'alive', 
                                    last_heartbeat_at = ?,
                                    active_rooms = ?,
                                    active_clients = ?,
                                    active_transfers = ?
                                WHERE server_id = ?
                            """, (now_str, stats.get('active_rooms', 0), stats.get('active_clients', 0), 
                                  stats.get('active_transfers', 0), server_id))
                        else:
                            cursor.execute("""
                                UPDATE backend_servers 
                                SET status = 'alive', last_heartbeat_at = ?
                                WHERE server_id = ?
                            """, (now_str, server_id))
                        db_conn.commit()
                except Exception as e:
                    self.logger.exception(f"Error updating heartbeat for {server_id} in DB: {e}")
            else:
                self.logger.warning(f"Received heartbeat from unknown backend: {server_id}")

    def get_alive_backends(self):
        with self.lock:
            return {sid: info for sid, info in self.backends.items() if info["status"] == "alive"}

    def _health_check_loop(self):
        while self.running:
            threading.Event().wait(5)
            self._check_timeouts()

    def _check_timeouts(self):
        now = datetime.now()
        timeout_threshold = now - timedelta(seconds=self.timeout_seconds)
        
        backends_to_mark_down = []
        
        with self.lock:
            for server_id, info in self.backends.items():
                if info["status"] == "alive" and info["last_heartbeat"] < timeout_threshold:
                    backends_to_mark_down.append(server_id)
                    info["status"] = "down"
        
        for server_id in backends_to_mark_down:
            self.logger.warning(f"Backend {server_id} timed out and marked as down.")
            try:
                with get_db_connection() as db_conn:
                    cursor = db_conn.cursor()
                    cursor.execute("UPDATE backend_servers SET status = 'down' WHERE server_id = ?", (server_id,))
                    db_conn.commit()
            except Exception as e:
                self.logger.error(f"Error marking backend {server_id} as down in DB: {e}")

    def stop(self):
        self.running = False
