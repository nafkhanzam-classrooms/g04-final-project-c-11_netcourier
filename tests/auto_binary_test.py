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
DOWNLOAD_DIR = "tests/download_temp"

def get_checksum(file_path):
    hash_sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_sha256.update(chunk)
    return hash_sha256.hexdigest()

def test_file_transfer(file_name):
    file_path = os.path.join(TEST_DIR, file_name)
    if not os.path.exists(file_path):
        print(f"Skipping {file_name}: File not found.")
        return False
    
    file_size = os.path.getsize(file_path)
    print(f"\n[TEST] Testing {file_name} ({file_size / 1024:.2f} KB)")
    
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        client.connect((GATEWAY_HOST, GATEWAY_PORT))
        
        # 1. Login or Register
        login_header = {
            "type": MESSAGE_TYPES["LOGIN"],
            "payload": {"username": "tester", "password": "password"}
        }
        send_packet(client, login_header)
        header, _ = receive_packet(client)
        
        if header.get("type") == "ERROR":
            reg_header = {
                "type": MESSAGE_TYPES["REGISTER"],
                "payload": {"username": "tester", "password": "password"}
            }
            send_packet(client, reg_header)
            receive_packet(client)
            send_packet(client, login_header)
            header, _ = receive_packet(client)
            
        token = header.get("token") or header.get("payload", {}).get("token")
        if not token:
            print(f"Auth failed: {header}")
            return False
        
        # 2. Join Room
        join_header = {"type": MESSAGE_TYPES["JOIN_ROOM"], "token": token, "payload": {"room_name": "Lobby"}}
        send_packet(client, join_header)
        header, _ = receive_packet(client)
        
        if header.get("type") == "ERROR":
            list_header = {"type": MESSAGE_TYPES["LIST_ROOMS"], "token": token}
            send_packet(client, list_header)
            h, _ = receive_packet(client)
            rooms = h.get("payload", {}).get("rooms", [])
            if not rooms: return False
            room_name = rooms[0].get("room_name") or rooms[0].get("name")
            join_header["payload"]["room_name"] = room_name
            send_packet(client, join_header)
            header, _ = receive_packet(client)

        room_payload = header["payload"]
        room_client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        room_client.connect((room_payload["host"], room_payload["port"]))
        
        # Auth Room
        auth_header = {"type": MESSAGE_TYPES["AUTH_BACKEND"], "token": token, "payload": {"room_id": room_payload["room_id"]}}
        send_packet(room_client, auth_header)
        receive_packet(room_client)
        
        # 4. Upload Init
        original_checksum = get_checksum(file_path)
        chunk_size = 1024 * 1024
        total_chunks = (file_size + chunk_size - 1) // chunk_size
        upload_init = {
            "type": MESSAGE_TYPES["UPLOAD_INIT"],
            "token": token,
            "payload": {
                "room_id": room_payload["room_id"],
                "filename": file_name,
                "filesize": file_size,
                "chunk_size": chunk_size,
                "total_chunks": total_chunks,
                "checksum_sha256": original_checksum
            }
        }
        send_packet(room_client, upload_init)
        header, _ = receive_packet(room_client)
        if header.get("type") == "ERROR":
            print(f"Upload Init failed: {header}")
            return False
        
        transfer_id = header["payload"]["transfer_id"]
        
        # 5. Upload Chunks
        with open(file_path, "rb") as f:
            idx = 0
            while True:
                data = f.read(chunk_size)
                if not data: break
                chunk_header = {
                    "type": MESSAGE_TYPES["UPLOAD_CHUNK"],
                    "token": token,
                    "payload": {"transfer_id": transfer_id, "chunk_index": idx, "chunk_size": len(data)}
                }
                send_packet(room_client, chunk_header, data)
                receive_packet(room_client)
                idx += 1
                if file_size > 5 * 1024 * 1024 and idx % 5 == 0:
                    print(f"Uploaded {f.tell() / file_size * 100:.1f}%...", end="\r")
        
        # 6. Upload Finish
        finish_header = {"type": MESSAGE_TYPES["UPLOAD_FINISH"], "token": token, "payload": {"transfer_id": transfer_id}}
        send_packet(room_client, finish_header)
        receive_packet(room_client)
        print(f"Upload {file_name} complete.         ")
        
        # 7. Get File ID from list
        list_req = {"type": MESSAGE_TYPES["FILE_LIST_REQUEST"], "token": token, "payload": {"room_name": room_payload["room_name"]}}
        send_packet(room_client, list_req)
        header, _ = receive_packet(room_client)
        files = header["payload"]["files"]
        file_id = next((f["file_id"] for f in files if f["original_filename"] == file_name), None)
        
        # 8. Download
        download_req = {"type": MESSAGE_TYPES["DOWNLOAD_REQUEST"], "token": token, "payload": {"file_id": file_id}}
        send_packet(room_client, download_req)
        header, _ = receive_packet(room_client)
        dl_transfer_id = header["payload"]["transfer_id"]
        
        if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)
        dl_path = os.path.join(DOWNLOAD_DIR, f"dl_{file_name}")
        
        with open(dl_path, "wb") as f:
            for i in range(header["payload"]["total_chunks"]):
                h, data = receive_packet(room_client)
                f.write(data)
        
        if get_checksum(dl_path) == original_checksum:
            print(f"SUCCESS: {file_name} verified!")
            return True
        return False
            
    except Exception as e:
        print(f"Error: {e}")
        return False
    finally:
        client.close()

if __name__ == "__main__":
    files = ["test_1kb.bin", "test_65kb.bin", "test_1mb.bin", "test_5mb.bin", "test_10mb.bin"]
    success = 0
    for f in files:
        if test_file_transfer(f): success += 1
    print(f"\nResult: {success}/{len(files)} passed.")
    sys.exit(0 if success == len(files) else 1)
