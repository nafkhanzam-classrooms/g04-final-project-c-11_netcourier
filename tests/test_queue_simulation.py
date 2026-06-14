import socket
import threading
import time
import os
import hashlib
from common.protocol import send_packet, receive_packet
from common.constants import MESSAGE_TYPES

GATEWAY_HOST = "127.0.0.1"
GATEWAY_PORT = 9000
TEST_DIR = "tests/uploadbinarytest"
MAX_CONCURRENT = 2

def get_checksum(file_path):
    hash_sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_sha256.update(chunk)
    return hash_sha256.hexdigest()

def upload_file_worker(index, file_name, results):
    username = f"tester_{index}"
    password = "password"
    file_path = os.path.join(TEST_DIR, file_name)
    file_size = os.path.getsize(file_path)
    checksum = get_checksum(file_path)
    
    # 1. Get Session & Room Info for this worker
    gw_client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        gw_client.connect((GATEWAY_HOST, GATEWAY_PORT))
        
        # Register if needed
        send_packet(gw_client, {"type": MESSAGE_TYPES["REGISTER"], "payload": {"username": username, "password": password, "display_name": username}})
        receive_packet(gw_client)
        
        send_packet(gw_client, {"type": MESSAGE_TYPES["LOGIN"], "payload": {"username": username, "password": password}})
        h, _ = receive_packet(gw_client)
        token = h.get("token") or h.get("payload", {}).get("token")
        
        send_packet(gw_client, {"type": MESSAGE_TYPES["JOIN_ROOM"], "token": token, "payload": {"room_name": "Lobby"}})
        h, _ = receive_packet(gw_client)
        
        if h.get("type") == "ERROR":
            send_packet(gw_client, {"type": MESSAGE_TYPES["LIST_ROOMS"], "token": token})
            h_list, _ = receive_packet(gw_client)
            rooms = h_list.get("payload", {}).get("rooms", [])
            room_name = rooms[0].get("room_name") or rooms[0].get("name")
            send_packet(gw_client, {"type": MESSAGE_TYPES["JOIN_ROOM"], "token": token, "payload": {"room_name": room_name}})
            h, _ = receive_packet(gw_client)
            
        room_payload = h["payload"]
        # gw_client.close()  <-- KEEP OPEN TO PREVENT SESSION CLEANUP
        
        # 2. Upload to Room Server
        room_client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        room_client.connect((room_payload["host"], room_payload["port"]))
        
        # Auth
        auth_header = {"type": MESSAGE_TYPES["AUTH_BACKEND"], "token": token, "payload": {"room_id": room_payload["room_id"]}}
        send_packet(room_client, auth_header)
        receive_packet(room_client)
        
        # Init
        chunk_size = 1024 * 1024
        total_chunks = (file_size + chunk_size - 1) // chunk_size
        init_packet = {
            "type": MESSAGE_TYPES["UPLOAD_INIT"],
            "token": token,
            "payload": {
                "room_id": room_payload["room_id"],
                "filename": file_name,
                "filesize": file_size,
                "chunk_size": chunk_size,
                "total_chunks": total_chunks,
                "checksum_sha256": checksum
            }
        }
        send_packet(room_client, init_packet)
        header, _ = receive_packet(room_client)
        
        if header.get("type") != "UPLOAD_READY":
            err_msg = header.get('payload', {}).get('message', 'No message')
            err_code = header.get('payload', {}).get('code', 'No code')
            results[file_name] = f"FAILED: Expected UPLOAD_READY, got {header.get('type')} ({err_code}: {err_msg})"
            print(f" [ERROR] {file_name}: {results[file_name]}")
            room_client.close()
            gw_client.close()
            return

        transfer_id = header["payload"]["transfer_id"]
        
        # Chunks
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
        
        # Finish
        finish_packet = {"type": MESSAGE_TYPES["UPLOAD_FINISH"], "token": token, "payload": {"transfer_id": transfer_id}}
        send_packet(room_client, finish_packet)
        receive_packet(room_client)
        
        results[file_name] = "SUCCESS"
        print(f" [DONE] {file_name}")
        room_client.close()
        gw_client.close()
    except Exception as e:
        results[file_name] = f"FAILED: {e}"
        print(f" [ERROR] {file_name}: {e}")
        try: gw_client.close()
        except: pass

def simulate_hybrid_queue():
    print("--- Simulating Hybrid Upload Queue (Max 2 Concurrent) ---")
    files_to_upload = ["test_1kb.bin", "test_65kb.bin", "test_1mb.bin", "test_5mb.bin"]
    results = {}
    active_threads = []
    
    print(f"Adding {len(files_to_upload)} files to simulated queue...")
    queue = list(enumerate(files_to_upload))
    
    while queue or active_threads:
        while len(active_threads) < MAX_CONCURRENT and queue:
            idx, f = queue.pop(0)
            print(f" [QUEUED] {f} (User tester_{idx})")
            # Stagger starts slightly to avoid DB locks during concurrent registration
            import random
            time.sleep(random.uniform(0.1, 0.5))
            print(f" [STARTING] {f}")
            t = threading.Thread(target=upload_file_worker, args=(idx, f, results))
            t.start()
            active_threads.append((f, t))
        
        for ft in active_threads[:]:
            if not ft[1].is_alive():
                active_threads.remove(ft)
        time.sleep(0.5)

    print("\n--- Final Simulation Results ---")
    for f, res in results.items():
        print(f"{f}: {res}")

if __name__ == "__main__":
    simulate_hybrid_queue()
