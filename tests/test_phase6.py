import unittest
import socket
import json
import time
import threading
from common.protocol import send_packet, receive_packet, build_packet

# --- Helper Client Class ---
class TestClient:
    def __init__(self, username, password):
        self.username = username
        self.password = password
        self.gateway_host = "127.0.0.1"
        self.gateway_port = 9000
        self.gateway_sock = None
        self.room_sock = None
        self.token = None
        self.user_id = None
        
        self.room_host = None
        self.room_port = None
        
        # Buffer for received room messages
        self.room_events = []
        self.running = False
        self.listener_thread = None

    def connect_gateway(self):
        self.gateway_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.gateway_sock.connect((self.gateway_host, self.gateway_port))

    def register_and_login(self):
        # Register
        req = build_packet("REGISTER", {
            "username": self.username,
            "password": self.password,
            "display_name": self.username
        })
        send_packet(self.gateway_sock, req)
        res, _ = receive_packet(self.gateway_sock)
        # It's okay if registration fails due to user already exists
        
        # Login
        req = build_packet("LOGIN", {
            "username": self.username,
            "password": self.password
        })
        send_packet(self.gateway_sock, req)
        res, _ = receive_packet(self.gateway_sock)
        if res["type"] == "LOGIN_OK":
            self.token = res["token"]
            self.user_id = res["payload"]["user_id"]
            return True
        return False

    def create_room(self, room_name):
        req = build_packet("CREATE_ROOM", {"room_name": room_name, "description": "Test Room"}, token=self.token)
        send_packet(self.gateway_sock, req)
        res, _ = receive_packet(self.gateway_sock)
        return res["type"] in ["ROOM_ASSIGNED", "ERROR"] # ERROR might be ROOM_ALREADY_EXISTS

    def join_room_gateway(self, room_name):
        req = build_packet("JOIN_ROOM", {"room_name": room_name}, token=self.token)
        send_packet(self.gateway_sock, req)
        res, _ = receive_packet(self.gateway_sock)
        if res["type"] == "ROOM_LOCATION":
            self.room_host = res["payload"]["host"]
            self.room_port = res["payload"]["port"]
            return True
        return False

    def connect_room_server(self, room_name):
        self.room_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.room_sock.connect((self.room_host, self.room_port))
        
        # Auth
        req = build_packet("AUTH_BACKEND", {}, token=self.token)
        send_packet(self.room_sock, req)
        res, _ = receive_packet(self.room_sock)
        
        if res["type"] == "AUTH_BACKEND_OK":
            # Join room on backend
            req = build_packet("JOIN_ROOM_BACKEND", {"room_name": room_name}, token=self.token)
            send_packet(self.room_sock, req)
            
            # Wait for JOIN_ROOM_OK, might get SYSTEM_EVENT first
            while True:
                res, _ = receive_packet(self.room_sock)
                if res["type"] == "JOIN_ROOM_OK":
                    self.running = True
                    self.listener_thread = threading.Thread(target=self._listen_room, daemon=True)
                    self.listener_thread.start()
                    return True
                elif res["type"] == "SYSTEM_EVENT":
                    self.room_events.append(res)
                else:
                    return False
        return False

    def request_history(self, room_name):
        req = build_packet("ROOM_HISTORY_REQUEST", {"room_name": room_name, "limit": 10}, token=self.token)
        send_packet(self.room_sock, req)

    def send_chat(self, room_name, message):
        req = build_packet("ROOM_CHAT_SEND", {"room_name": room_name, "message": message}, token=self.token)
        send_packet(self.room_sock, req)

    def _listen_room(self):
        self.room_sock.settimeout(1.0)
        while self.running:
            try:
                res, _ = receive_packet(self.room_sock)
                if res:
                    self.room_events.append(res)
            except socket.timeout:
                continue
            except Exception:
                break

    def get_events_and_clear(self):
        events = self.room_events.copy()
        self.room_events.clear()
        return events

    def close(self):
        self.running = False
        if self.listener_thread:
            self.listener_thread.join(timeout=2)
        if self.gateway_sock:
            self.gateway_sock.close()
        if self.room_sock:
            self.room_sock.close()


class TestPhase6(unittest.TestCase):
    """
    Pastikan Gateway dan Process Server (S1) sudah berjalan sebelum menjalankan test ini.
    Gateway di 127.0.0.1:9000
    Process Server di port yang didaftarkan ke Gateway (S1 misalnya 9002)
    """
    
    @classmethod
    def setUpClass(cls):
        # We assume the environment is already running for a black-box integration test.
        cls.room_name = f"TestRoomPhase6_{int(time.time())}"

    def test_01_chat_and_history(self):
        # Setup Client A
        client_a = TestClient("user_a_ph6", "pass123")
        client_a.connect_gateway()
        self.assertTrue(client_a.register_and_login(), "Client A failed to login")
        
        # Create Room
        client_a.create_room(self.room_name)
        
        # Join Gateway & Server
        self.assertTrue(client_a.join_room_gateway(self.room_name), "Client A failed to get room location")
        self.assertTrue(client_a.connect_room_server(self.room_name), "Client A failed to connect to room server")
        
        # Check initial join SYSTEM_EVENT (Wait a moment for broadcast)
        time.sleep(0.5)
        events_a = client_a.get_events_and_clear()
        sys_events_a = [e for e in events_a if e["type"] == "SYSTEM_EVENT"]
        self.assertTrue(len(sys_events_a) >= 1, "Client A should receive SYSTEM_EVENT on join")

        # Setup Client B
        client_b = TestClient("user_b_ph6", "pass123")
        client_b.connect_gateway()
        self.assertTrue(client_b.register_and_login(), "Client B failed to login")
        
        self.assertTrue(client_b.join_room_gateway(self.room_name), "Client B failed to get room location")
        self.assertTrue(client_b.connect_room_server(self.room_name), "Client B failed to connect to room server")
        
        # Check that Client A receives User B's join event
        time.sleep(0.5)
        events_a = client_a.get_events_and_clear()
        join_b_event = [e for e in events_a if e["type"] == "SYSTEM_EVENT" and "user_b_ph6" in e["payload"]["message"]]
        self.assertTrue(len(join_b_event) > 0, "Client A did not receive User B join system event")

        # 1. Test Broadcasting
        msg_text = "Hello from Client A Phase 6!"
        client_a.send_chat(self.room_name, msg_text)
        
        time.sleep(0.5)
        
        # Check if B received the chat
        events_b = client_b.get_events_and_clear()
        chats_b = [e for e in events_b if e["type"] == "ROOM_CHAT_BROADCAST" and e["payload"]["message"] == msg_text]
        self.assertTrue(len(chats_b) > 0, "Client B did not receive the broadcast message from A")

        # Check if A received the chat echo
        events_a = client_a.get_events_and_clear()
        chats_a = [e for e in events_a if e["type"] == "ROOM_CHAT_BROADCAST" and e["payload"]["message"] == msg_text]
        self.assertTrue(len(chats_a) > 0, "Client A did not receive the broadcast message echo")

        # 2. Test History
        # Setup Client C to join and request history
        client_c = TestClient("user_c_ph6", "pass123")
        client_c.connect_gateway()
        client_c.register_and_login()
        client_c.join_room_gateway(self.room_name)
        client_c.connect_room_server(self.room_name)
        
        # clear initial events
        time.sleep(0.5)
        client_c.get_events_and_clear()
        
        # Request history
        client_c.request_history(self.room_name)
        
        time.sleep(0.5)
        events_c = client_c.get_events_and_clear()
        history_response = [e for e in events_c if e["type"] == "ROOM_HISTORY_RESPONSE"]
        self.assertTrue(len(history_response) > 0, "Client C did not receive ROOM_HISTORY_RESPONSE")
        
        history_msgs = history_response[0]["payload"]["messages"]
        # History might include some system messages from earlier if saved, but text definitely
        chat_history = [m for m in history_msgs if m["message_type"] == "text" and m["message"] == msg_text]
        self.assertTrue(len(chat_history) > 0, "Client C history did not contain the previous chat message")

        # 3. Test Room Isolation (User in a different room does not receive messages)
        room2_name = self.room_name + "_ISOLATED"
        client_d = TestClient("user_d_ph6", "pass123")
        client_d.connect_gateway()
        client_d.register_and_login()
        client_d.create_room(room2_name)
        client_d.join_room_gateway(room2_name)
        client_d.connect_room_server(room2_name)
        
        # Clear client D's buffer from any initial join events
        time.sleep(0.5)
        client_d.get_events_and_clear()
        
        # Client A (in Room 1) sends a new message
        isolated_msg = "This message should not leak to Room 2"
        client_a.send_chat(self.room_name, isolated_msg)
        time.sleep(0.5)
        
        # Check Client B (in Room 1) receives it
        events_b2 = client_b.get_events_and_clear()
        chats_b2 = [e for e in events_b2 if e["type"] == "ROOM_CHAT_BROADCAST" and e["payload"]["message"] == isolated_msg]
        self.assertTrue(len(chats_b2) > 0, "Client B did not receive the second broadcast message")
        
        # Check Client D (in Room 2) does NOT receive it
        events_d = client_d.get_events_and_clear()
        chats_d = [e for e in events_d if e["type"] == "ROOM_CHAT_BROADCAST" and e["payload"]["message"] == isolated_msg]
        self.assertEqual(len(chats_d), 0, "Client D incorrectly received a message from a different room!")

        # Cleanup
        client_a.close()
        client_b.close()
        client_c.close()
        client_d.close()


if __name__ == "__main__":
    unittest.main()
