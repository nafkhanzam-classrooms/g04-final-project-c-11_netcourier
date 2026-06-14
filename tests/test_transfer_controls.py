import socket
import time
import os
import hashlib
from common.protocol import send_packet, receive_packet
from common.constants import MESSAGE_TYPES

GATEWAY_HOST = "127.0.0.1"
GATEWAY_PORT = 9000
TEST_DIR = "tests/uploadbinarytest"
FILE_NAME = "test_10mb.bin"

def get_checksum(file_path):
    hash_sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_sha256.update(chunk)
    return hash_sha256.hexdigest()

def test_pause_resume():
    print("\n--- Testing PAUSE & RESUME Logic ---")
    file_path = os.path.join(TEST_DIR, FILE_NAME)
    file_size = os.path.getsize(file_path)
    original_checksum = get_checksum(file_path)
    
    # 1. Login & Join Room
    gw_client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    gw_client.connect((GATEWAY_HOST, GATEWAY_PORT))
    # Register "tester" (ignore error if username taken)
    send_packet(gw_client, {"type": MESSAGE_TYPES["REGISTER"], "payload": {"username": "tester", "password": "password", "display_name": "Tester"}})
    receive_packet(gw_client)
    
    send_packet(gw_client, {"type": MESSAGE_TYPES["LOGIN"], "payload": {"username": "tester", "password": "password"}})
    h, _ = receive_packet(gw_client)
    token = h.get("token") or h.get("payload", {}).get("token")
    
    # Create Lobby room
    send_packet(gw_client, {"type": MESSAGE_TYPES["CREATE_ROOM"], "token": token, "payload": {"room_name": "Lobby", "description": "Lobby room"}})
    receive_packet(gw_client)
    
    # Try join Lobby
    send_packet(gw_client, {"type": MESSAGE_TYPES["JOIN_ROOM"], "token": token, "payload": {"room_name": "Lobby"}})
    h, _ = receive_packet(gw_client)
    
    if h.get("type") == "ERROR":
        # Get list of rooms
        send_packet(gw_client, {"type": MESSAGE_TYPES["LIST_ROOMS"], "token": token})
        h_list, _ = receive_packet(gw_client)
        rooms = h_list.get("payload", {}).get("rooms", [])
        if not rooms:
            print("No rooms available to test.")
            return False
        room_name = rooms[0].get("room_name") or rooms[0].get("name")
        send_packet(gw_client, {"type": MESSAGE_TYPES["JOIN_ROOM"], "token": token, "payload": {"room_name": room_name}})
        h, _ = receive_packet(gw_client)

    room_payload = h["payload"]
    # gw_client.close()  <-- KEEP OPEN TO PREVENT SESSION CLEANUP

    # 2. Start Upload (Initial)
    room_client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    room_client.connect((room_payload["host"], room_payload["port"]))
    send_packet(room_client, {"type": MESSAGE_TYPES["AUTH_BACKEND"], "token": token, "payload": {"room_id": room_payload["room_id"]}})
    receive_packet(room_client)

    chunk_size = 1024 * 1024 # 1MB
    total_chunks = (file_size + chunk_size - 1) // chunk_size
    
    print(f"Starting upload of {FILE_NAME}...")
    send_packet(room_client, {
        "type": MESSAGE_TYPES["UPLOAD_INIT"], "token": token,
        "payload": {
            "room_id": room_payload["room_id"], "filename": FILE_NAME,
            "filesize": file_size, "chunk_size": chunk_size,
            "total_chunks": total_chunks, "checksum_sha256": original_checksum
        }
    })
    h, _ = receive_packet(room_client)
    
    if h.get("type") != "UPLOAD_READY":
        print(f"FAILED: Expected UPLOAD_READY, got {h.get('type')} ({h.get('payload', {}).get('message')})")
        return False
        
    transfer_id = h["payload"]["transfer_id"]

    # Send 3 chunks and then "PAUSE" (Disconnect)
    with open(file_path, "rb") as f:
        for i in range(3):
            data = f.read(chunk_size)
            send_packet(room_client, {
                "type": MESSAGE_TYPES["UPLOAD_CHUNK"], "token": token,
                "payload": {"transfer_id": transfer_id, "chunk_index": i, "chunk_size": len(data)}
            }, data)
            receive_packet(room_client)
            print(f" Sent chunk {i}")
    
    print("!! Simulating PAUSE (Closing Connection) !!")
    room_client.close()
    time.sleep(1)

    # 3. RESUME
    print("Attempting to RESUME...")
    room_client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    room_client.connect((room_payload["host"], room_payload["port"]))
    send_packet(room_client, {"type": MESSAGE_TYPES["AUTH_BACKEND"], "token": token, "payload": {"room_id": room_payload["room_id"]}})
    receive_packet(room_client)

    # Check progress
    send_packet(room_client, {
        "type": MESSAGE_TYPES["RESUME_TRANSFER"], "token": token,
        "payload": {"transfer_id": transfer_id, "direction": "upload"}
    })
    h, _ = receive_packet(room_client)
    start_chunk = h["payload"]["start_chunk"]
    print(f" Server says resume from chunk: {start_chunk}")

    if start_chunk != 3:
        print(f"FAILED: Expected server to have 3 chunks, but got {start_chunk}")
        return False

    # Continue from start_chunk
    with open(file_path, "rb") as f:
        f.seek(start_chunk * chunk_size)
        for i in range(start_chunk, total_chunks):
            data = f.read(chunk_size)
            send_packet(room_client, {
                "type": MESSAGE_TYPES["UPLOAD_CHUNK"], "token": token,
                "payload": {"transfer_id": transfer_id, "chunk_index": i, "chunk_size": len(data)}
            }, data)
            receive_packet(room_client)
            print(f" Sent resumed chunk {i}")

    send_packet(room_client, {"type": MESSAGE_TYPES["UPLOAD_FINISH"], "token": token, "payload": {"transfer_id": transfer_id}})
    h, _ = receive_packet(room_client)
    
    if h.get("type") == "UPLOAD_SUCCESS":
        print("SUCCESS: Pause & Resume verified!")
        return True
    else:
        print(f"FAILED: Finish failed: {h}")
        return False

if __name__ == "__main__":
    if test_pause_resume():
        print("\nALL CONTROL TESTS PASSED")
    else:
        print("\nCONTROL TESTS FAILED")
        exit(1)
