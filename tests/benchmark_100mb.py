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
TEST_DIR = "tests/uploadbinarytest"

def get_checksum(file_path):
    hash_sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_sha256.update(chunk)
    return hash_sha256.hexdigest()

def benchmark_transfer(file_name):
    file_path = os.path.join(TEST_DIR, file_name)
    if not os.path.exists(file_path):
        print(f"Skipping {file_name}: File not found.")
        return False
    
    file_size = os.path.getsize(file_path)
    print(f"\n[BENCHMARK] Testing {file_name} ({file_size / (1024*1024):.2f} MB)")
    
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        client.connect((GATEWAY_HOST, GATEWAY_PORT))
        
        # 1. Login
        send_packet(client, {"type": MESSAGE_TYPES["LOGIN"], "payload": {"username": "tester", "password": "password"}})
        header, _ = receive_packet(client)
        token = header.get("token") or header.get("payload", {}).get("token")
        
        # 2. Join Room
        send_packet(client, {"type": MESSAGE_TYPES["JOIN_ROOM"], "token": token, "payload": {"room_name": "Lobby"}})
        header, _ = receive_packet(client)
        
        if header.get("type") == "ERROR":
            send_packet(client, {"type": MESSAGE_TYPES["LIST_ROOMS"], "token": token})
            h_list, _ = receive_packet(client)
            rooms = h_list.get("payload", {}).get("rooms", [])
            if not rooms: return False
            room_name = rooms[0].get("room_name") or rooms[0].get("name")
            send_packet(client, {"type": MESSAGE_TYPES["JOIN_ROOM"], "token": token, "payload": {"room_name": room_name}})
            header, _ = receive_packet(client)

        room_payload = header["payload"]
        
        # 3. Connect to Room Server
        room_client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        room_client.connect((room_payload["host"], room_payload["port"]))
        send_packet(room_client, {"type": MESSAGE_TYPES["AUTH_BACKEND"], "token": token, "payload": {"room_id": room_payload["room_id"]}})
        receive_packet(room_client)
        
        # 4. Benchmark Upload
        start_time = time.time()
        chunk_size = 1024 * 1024 # 1MB
        total_chunks = (file_size + chunk_size - 1) // chunk_size
        original_checksum = get_checksum(file_path)
        
        send_packet(room_client, {
            "type": MESSAGE_TYPES["UPLOAD_INIT"], "token": token,
            "payload": {
                "room_id": room_payload["room_id"], "filename": file_name,
                "filesize": file_size, "chunk_size": chunk_size,
                "total_chunks": total_chunks, "checksum_sha256": original_checksum
            }
        })
        h, _ = receive_packet(room_client)
        transfer_id = h["payload"]["transfer_id"]
        
        with open(file_path, "rb") as f:
            for i in range(total_chunks):
                data = f.read(chunk_size)
                send_packet(room_client, {
                    "type": MESSAGE_TYPES["UPLOAD_CHUNK"], "token": token,
                    "payload": {"transfer_id": transfer_id, "chunk_index": i, "chunk_size": len(data)}
                }, data)
                receive_packet(room_client)
                
        send_packet(room_client, {"type": MESSAGE_TYPES["UPLOAD_FINISH"], "token": token, "payload": {"transfer_id": transfer_id}})
        receive_packet(room_client)
        
        end_time = time.time()
        duration = end_time - start_time
        speed = (file_size / (1024*1024)) / duration
        print(f"UPLOAD SUCCESS: {file_size / (1024*1024):.2f} MB in {duration:.2f}s ({speed:.2f} MB/s)")
        
        return True
            
    except Exception as e:
        print(f"Benchmark Error: {e}")
        return False
    finally:
        client.close()

if __name__ == "__main__":
    benchmark_transfer("test_1gb.bin")
