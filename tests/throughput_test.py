import socket
import json
import os
import hashlib
import time
import sys
from common.protocol import send_packet, receive_packet
from common.constants import MESSAGE_TYPES

GATEWAY_HOST = "127.0.0.1"
GATEWAY_PORT = 9000
TEST_FILE = "tests/uploadbinarytest/test_1gb.bin"
DOWNLOAD_DIR = "tests/download_temp"

def get_checksum(file_path):
    hash_sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_sha256.update(chunk)
    return hash_sha256.hexdigest()

def run_throughput_benchmark():
    if not os.path.exists(TEST_FILE):
        print(f"Error: {TEST_FILE} not found.")
        return
    
    file_size = os.path.getsize(TEST_FILE)
    file_name = os.path.basename(TEST_FILE)
    print(f"--- Starting 1GB Throughput Benchmark ---")
    print(f"File: {file_name} ({file_size / (1024**3):.2f} GB)")

    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        client.connect((GATEWAY_HOST, GATEWAY_PORT))
        
        # 1. Auth
        # Register "tester" (ignore error if username taken)
        send_packet(client, {"type": MESSAGE_TYPES["REGISTER"], "payload": {"username": "tester", "password": "password", "display_name": "Tester"}})
        receive_packet(client)
        
        send_packet(client, {"type": MESSAGE_TYPES["LOGIN"], "payload": {"username": "tester", "password": "password"}})
        h, _ = receive_packet(client)
        token = h.get("token") or h.get("payload", {}).get("token")
        
        # Create General room
        send_packet(client, {"type": MESSAGE_TYPES["CREATE_ROOM"], "token": token, "payload": {"room_name": "General", "description": "General room"}})
        receive_packet(client)
        
        # 2. Join Room
        send_packet(client, {"type": MESSAGE_TYPES["JOIN_ROOM"], "token": token, "payload": {"room_name": "General"}})
        h, _ = receive_packet(client)
        if h.get("type") == "ERROR":
            # Try Lobby if General not found
            send_packet(client, {"type": MESSAGE_TYPES["JOIN_ROOM"], "token": token, "payload": {"room_name": "Lobby"}})
            h, _ = receive_packet(client)
            
        room_payload = h["payload"]
        # client.close()  # <--- DO NOT CLOSE

        # 3. Connect to Room
        room_client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        room_client.connect((room_payload["host"], room_payload["port"]))
        send_packet(room_client, {"type": MESSAGE_TYPES["AUTH_BACKEND"], "token": token, "payload": {"room_id": room_payload["room_id"]}})
        receive_packet(room_client)

        chunk_size = 1024 * 1024 # 1MB
        total_chunks = (file_size + chunk_size - 1) // chunk_size
        checksum = get_checksum(TEST_FILE)

        # --- UPLOAD BENCHMARK ---
        print("\n[STEP 1] Starting Upload Benchmark...")
        start_time = time.time()
        
        send_packet(room_client, {
            "type": MESSAGE_TYPES["UPLOAD_INIT"], "token": token,
            "payload": {
                "room_id": room_payload["room_id"], "filename": file_name,
                "filesize": file_size, "chunk_size": chunk_size,
                "total_chunks": total_chunks, "checksum_sha256": checksum
            }
        })
        h, _ = receive_packet(room_client)
        if h.get("type") != "UPLOAD_READY":
            print(f"Error: Expected UPLOAD_READY, got {h.get('type')} ({h.get('payload', {}).get('message')})")
            client.close()
            return
        transfer_id = h["payload"]["transfer_id"]

        with open(TEST_FILE, "rb") as f:
            for i in range(total_chunks):
                data = f.read(chunk_size)
                send_packet(room_client, {
                    "type": MESSAGE_TYPES["UPLOAD_CHUNK"], "token": token,
                    "payload": {"transfer_id": transfer_id, "chunk_index": i, "chunk_size": len(data)}
                }, data)
                receive_packet(room_client)
                if i % 100 == 0:
                    print(f" Uploading: {i/total_chunks*100:.1f}%", end="\r")

        send_packet(room_client, {"type": MESSAGE_TYPES["UPLOAD_FINISH"], "token": token, "payload": {"transfer_id": transfer_id}})
        receive_packet(room_client)
        
        upload_duration = time.time() - start_time
        upload_speed = (file_size / (1024**2)) / upload_duration
        print(f"UPLOAD COMPLETE: {upload_duration:.2f}s ({upload_speed:.2f} MB/s)")

        # --- DOWNLOAD BENCHMARK ---
        print("\n[STEP 2] Starting Download Benchmark...")
        # Get file_id
        send_packet(room_client, {"type": MESSAGE_TYPES["FILE_LIST_REQUEST"], "token": token, "payload": {"room_name": room_payload["room_name"]}})
        h, _ = receive_packet(room_client)
        files = h["payload"]["files"]
        file_id = next((f["file_id"] for f in files if f["original_filename"] == file_name), None)

        if file_id is None:
            print("Error: Could not find uploaded file in list.")
            client.close()
            return

        start_time = time.time()
        send_packet(room_client, {"type": MESSAGE_TYPES["DOWNLOAD_REQUEST"], "token": token, "payload": {"file_id": file_id}})
        h, _ = receive_packet(room_client)
        dl_transfer_id = h["payload"]["transfer_id"]
        dl_total_chunks = h["payload"]["total_chunks"]

        if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)
        dl_path = os.path.join(DOWNLOAD_DIR, f"bench_{file_name}")

        with open(dl_path, "wb") as f:
            for i in range(dl_total_chunks):
                h, data = receive_packet(room_client)
                f.write(data)
                if i % 100 == 0:
                    print(f" Downloading: {i/dl_total_chunks*100:.1f}%", end="\r")

        download_duration = time.time() - start_time
        download_speed = (file_size / (1024**2)) / download_duration
        print(f"DOWNLOAD COMPLETE: {download_duration:.2f}s ({download_speed:.2f} MB/s)")

        # Verify Integrity
        print("\n[STEP 3] Verifying Integrity...")
        dl_checksum = get_checksum(dl_path)
        if dl_checksum == checksum:
            print("INTEGRITY CHECK: PASSED (SHA-256 match)")
        else:
            print("INTEGRITY CHECK: FAILED (Mismatch!)")

        client.close()
    except Exception as e:
        print(f"Benchmark Error: {e}")
        try: client.close()
        except: pass
    finally:
        room_client.close()

if __name__ == "__main__":
    run_throughput_benchmark()
