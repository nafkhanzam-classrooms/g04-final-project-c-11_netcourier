import unittest
import socket
import json
import time
import threading
import os
import hashlib
import struct
from common.protocol import send_packet, receive_packet, build_packet

# Reuse TestClient from phase 6, slightly modified for binary payload support
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
        self.room_events = []
        self.running = False
        self.listener_thread = None
        self.downloaded_chunks = {}

    def connect_gateway(self):
        self.gateway_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.gateway_sock.connect((self.gateway_host, self.gateway_port))

    def register_and_login(self):
        send_packet(self.gateway_sock, build_packet("REGISTER", {"username": self.username, "password": self.password, "display_name": self.username}))
        receive_packet(self.gateway_sock)
        send_packet(self.gateway_sock, build_packet("LOGIN", {"username": self.username, "password": self.password}))
        res, _ = receive_packet(self.gateway_sock)
        if res["type"] == "LOGIN_OK":
            self.token = res["token"]
            self.user_id = res["payload"]["user_id"]
            return True
        return False

    def create_room(self, room_name):
        send_packet(self.gateway_sock, build_packet("CREATE_ROOM", {"room_name": room_name, "description": "Test Room"}, token=self.token))
        res, _ = receive_packet(self.gateway_sock)
        return res["type"] in ["ROOM_ASSIGNED", "ERROR"]

    def join_room_gateway(self, room_name):
        send_packet(self.gateway_sock, build_packet("JOIN_ROOM", {"room_name": room_name}, token=self.token))
        res, _ = receive_packet(self.gateway_sock)
        if res["type"] == "ROOM_LOCATION":
            self.room_host = res["payload"]["host"]
            self.room_port = res["payload"]["port"]
            return True
        return False

    def connect_room_server(self, room_name):
        self.room_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.room_sock.connect((self.room_host, self.room_port))
        send_packet(self.room_sock, build_packet("AUTH_BACKEND", {}, token=self.token))
        res, _ = receive_packet(self.room_sock)
        if res["type"] == "AUTH_BACKEND_OK":
            send_packet(self.room_sock, build_packet("JOIN_ROOM_BACKEND", {"room_name": room_name}, token=self.token))
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

    def _listen_room(self):
        self.room_sock.settimeout(1.0)
        while self.running:
            try:
                res, b_payload = receive_packet(self.room_sock)
                if res:
                    if res.get("type") == "DOWNLOAD_CHUNK":
                        self.downloaded_chunks[res["payload"]["chunk_index"]] = b_payload
                    else:
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

class TestPhase7(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.room_name = f"TestRoomPhase7_{int(time.time())}"
        cls.test_dir = "tests/uploadbinarytest"
        cls.test_files = ["test_1mb.bin", "test_5mb.bin", "test_10mb.bin"]
        sizes = [1, 5, 10]
        # Dummy file creation
        for i, file_name in enumerate(cls.test_files):
            file_path = os.path.join(cls.test_dir, file_name)
            if not os.path.exists(file_path):
                with open(file_path, "wb") as f:
                    f.write(os.urandom(sizes[i] * 1024 * 1024))
                
    def test_01_upload_download_all_sizes(self):
        client_a = TestClient("user_a_ph7", "pass123")
        client_a.connect_gateway()
        self.assertTrue(client_a.register_and_login())
        client_a.create_room(self.room_name)
        self.assertTrue(client_a.join_room_gateway(self.room_name))
        self.assertTrue(client_a.connect_room_server(self.room_name))
        
        for test_file in self.test_files:
            file_path = os.path.join(self.test_dir, test_file)
            with self.subTest(file=test_file):
                print(f"\n--- Testing Transfer for {test_file} ---")
                # Reset downloaded chunks for each file
                client_a.downloaded_chunks = {}
                
                # Calculate Checksum
                sha256 = hashlib.sha256()
                with open(file_path, "rb") as f:
                    for chunk in iter(lambda: f.read(65536), b""):
                        sha256.update(chunk)
                checksum = sha256.hexdigest()
                filesize = os.path.getsize(file_path)
                chunk_size = 65536
                total_chunks = (filesize + chunk_size - 1) // chunk_size
                
                # 1. UPLOAD_INIT
                send_packet(client_a.room_sock, build_packet("UPLOAD_INIT", {
                    "room_name": self.room_name,
                    "filename": test_file,
                    "filesize": filesize,
                    "chunk_size": chunk_size,
                    "total_chunks": total_chunks,
                    "checksum_sha256": checksum
                }, token=client_a.token))
                
                time.sleep(0.5)
                events = client_a.get_events_and_clear()
                upload_ready = next((e for e in events if e["type"] == "UPLOAD_READY"), None)
                self.assertIsNotNone(upload_ready, f"Did not receive UPLOAD_READY for {test_file}")
                transfer_id = upload_ready["payload"]["transfer_id"]
                
                # 2. UPLOAD_CHUNK
                print(f"Uploading {total_chunks} chunks...")
                with open(file_path, "rb") as f:
                    for i in range(total_chunks):
                        chunk_data = f.read(chunk_size)
                        packet = build_packet("UPLOAD_CHUNK", {"transfer_id": transfer_id, "chunk_index": i}, token=client_a.token)
                        packet["payload_size"] = len(chunk_data)
                        header_json = json.dumps(packet).encode('utf-8')
                        client_a.room_sock.sendall(struct.pack(">I", len(header_json)) + header_json + chunk_data)
                        
                time.sleep(max(1, total_chunks * 0.01)) # Give server time to process
                events = client_a.get_events_and_clear()
                acks = [e for e in events if e["type"] == "CHUNK_ACK"]
                
                # We might not receive all ACKs immediately due to TCP buffering/timing in this simple test script,
                # but we will rely on UPLOAD_SUCCESS as the ultimate source of truth.
                
                # 3. UPLOAD_FINISH
                send_packet(client_a.room_sock, build_packet("UPLOAD_FINISH", {"transfer_id": transfer_id}, token=client_a.token))
                time.sleep(max(1, total_chunks * 0.05)) # Give server time to checksum
                events = client_a.get_events_and_clear()
                upload_success = next((e for e in events if e["type"] == "UPLOAD_SUCCESS"), None)
                
                # If we got an error, it might be in the events
                if not upload_success:
                    error_evt = next((e for e in events if e["type"] == "ERROR"), None)
                    print(f"Error Event if any: {error_evt}")
                
                self.assertIsNotNone(upload_success, f"Did not receive UPLOAD_SUCCESS for {test_file}. Checksum may have failed.")
                
                # 4. FILE_LIST_REQUEST
                send_packet(client_a.room_sock, build_packet("FILE_LIST_REQUEST", {"room_name": self.room_name}, token=client_a.token))
                time.sleep(0.5)
                events = client_a.get_events_and_clear()
                file_list_res = next((e for e in events if e["type"] == "FILE_LIST_RESPONSE"), None)
                self.assertIsNotNone(file_list_res, f"Did not receive FILE_LIST_RESPONSE for {test_file}")
                
                # Find the specific file we just uploaded
                uploaded_file_info = next((f for f in file_list_res["payload"]["files"] if f["original_filename"] == test_file), None)
                self.assertIsNotNone(uploaded_file_info, f"File {test_file} not found in FILE_LIST_RESPONSE")
                uploaded_file_id = uploaded_file_info["file_id"]
                
                # 5. DOWNLOAD_REQUEST
                print(f"Downloading {total_chunks} chunks...")
                send_packet(client_a.room_sock, build_packet("DOWNLOAD_REQUEST", {"file_id": uploaded_file_id}, token=client_a.token))
                time.sleep(0.5)
                events = client_a.get_events_and_clear()
                download_ready = next((e for e in events if e["type"] == "DOWNLOAD_READY"), None)
                self.assertIsNotNone(download_ready, f"Did not receive DOWNLOAD_READY for {test_file}")
                
                dl_total_chunks = download_ready["payload"]["total_chunks"]
                self.assertEqual(dl_total_chunks, total_chunks, "Downloaded chunks mismatch")
                
                # Wait for all chunks to download
                timeout = max(5, total_chunks * 0.05)
                start_time = time.time()
                while len(client_a.downloaded_chunks) < total_chunks and (time.time() - start_time) < timeout:
                    time.sleep(0.1)
                    
                self.assertEqual(len(client_a.downloaded_chunks), total_chunks, f"Did not receive all DOWNLOAD_CHUNKs for {test_file}")
                
                # Combine and verify
                dl_checksum = hashlib.sha256()
                for i in range(total_chunks):
                    dl_checksum.update(client_a.downloaded_chunks[i])
                self.assertEqual(dl_checksum.hexdigest(), checksum, f"Downloaded file checksum does not match original for {test_file}")
                print(f"Transfer successful and verified for {test_file}!")
        
        client_a.close()

if __name__ == "__main__":
    unittest.main()
