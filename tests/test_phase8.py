import os
import sys
import socket
import json
import struct
import time
import threading
import hashlib
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from common.protocol import build_packet, receive_packet, send_packet

GATEWAY_HOST = "127.0.0.1"
GATEWAY_PORT = 9000
SERVER_PORT = 9101 # Server 1

def connect_to_gateway():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((GATEWAY_HOST, GATEWAY_PORT))
    return sock

def connect_to_server():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect(("127.0.0.1", SERVER_PORT))
    return sock

def wait_for_packet(sock, msg_type):
    while True:
        header, payload = receive_packet(sock)
        if header["type"] == msg_type:
            return header, payload

def test_resume_upload():
    print("--- Test Resume Upload ---")
    # 1. Login to Gateway
    gw_sock = connect_to_gateway()
    username = f"user_{int(time.time())}"
    send_packet(gw_sock, build_packet("REGISTER", {"username": username, "password": "password"}))
    receive_packet(gw_sock)
    send_packet(gw_sock, build_packet("LOGIN", {"username": username, "password": "password"}))
    header, _ = receive_packet(gw_sock)
    token = header["token"]
    
    # Create room
    send_packet(gw_sock, build_packet("CREATE_ROOM", {"room_name": "General"}, token=token))
    receive_packet(gw_sock)
    
    # 2. Join room via Gateway to get server location
    send_packet(gw_sock, build_packet("JOIN_ROOM", {"room_name": "General"}, token=token))
    header, _ = receive_packet(gw_sock)
    print("JOIN_ROOM header:", header)
    server_host = header["payload"]["host"]
    server_port = header["payload"]["port"]
    print(f"Assigned to server {server_host}:{server_port}")
    
    # 3. Connect to Process Server
    srv_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv_sock.connect((server_host, server_port))
    send_packet(srv_sock, build_packet("AUTH_BACKEND", {"token": token}, token=token))
    h1, _ = wait_for_packet(srv_sock, "AUTH_BACKEND_OK")
    send_packet(srv_sock, build_packet("JOIN_ROOM_BACKEND", {"room_name": "General"}, token=token))
    h2, _ = wait_for_packet(srv_sock, "JOIN_ROOM_OK")
    
    # 4. Initiate Upload
    file_size = 1000000 # 1 MB
    chunk_size = 65536
    total_chunks = (file_size + chunk_size - 1) // chunk_size
    dummy_data = os.urandom(file_size)
    sha256 = hashlib.sha256(dummy_data).hexdigest()
    
    send_packet(srv_sock, build_packet("UPLOAD_INIT", {
        "room_name": "General",
        "filename": "test_resume.bin",
        "filesize": file_size,
        "chunk_size": chunk_size,
        "total_chunks": total_chunks,
        "checksum_sha256": sha256
    }, token=token))
    
    header, _ = wait_for_packet(srv_sock, "UPLOAD_READY")
    transfer_id = header["payload"]["transfer_id"]
    print(f"Upload initiated. Transfer ID: {transfer_id}")
    
    # 5. Send half of the chunks
    start_chunk = header["payload"]["start_chunk"]
    half_chunks = total_chunks // 2
    for i in range(start_chunk, half_chunks):
        chunk_data = dummy_data[i*chunk_size : (i+1)*chunk_size]
        packet = build_packet("UPLOAD_CHUNK", {
            "transfer_id": transfer_id,
            "chunk_index": i
        }, token=token)
        packet["payload_size"] = len(chunk_data)
        header_json = json.dumps(packet).encode('utf-8')
        srv_sock.sendall(struct.pack(">I", len(header_json)) + header_json + chunk_data)
        
        # Wait for CHUNK_ACK
        wait_for_packet(srv_sock, "CHUNK_ACK")
        
    print(f"Sent {half_chunks} chunks. Disconnecting...")
    srv_sock.close()
    
    # 6. Reconnect to Process Server
    srv_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv_sock.connect((server_host, server_port))
    send_packet(srv_sock, build_packet("AUTH_BACKEND", {"token": token}, token=token))
    wait_for_packet(srv_sock, "AUTH_BACKEND_OK")
    send_packet(srv_sock, build_packet("JOIN_ROOM_BACKEND", {"room_name": "General"}, token=token))
    wait_for_packet(srv_sock, "JOIN_ROOM_OK")
    
    # 7. Resume Transfer
    send_packet(srv_sock, build_packet("RESUME_TRANSFER", {
        "transfer_id": transfer_id,
        "direction": "upload"
    }, token=token))
    
    header, _ = wait_for_packet(srv_sock, "UPLOAD_READY")
    assert header["type"] == "UPLOAD_READY"
    resume_start_chunk = header["payload"]["start_chunk"]
    print(f"Resuming from chunk {resume_start_chunk}")
    assert resume_start_chunk == half_chunks, f"Expected {half_chunks}, got {resume_start_chunk}"
    
    # 8. Send remaining chunks
    for i in range(resume_start_chunk, total_chunks):
        chunk_data = dummy_data[i*chunk_size : (i+1)*chunk_size]
        packet = build_packet("UPLOAD_CHUNK", {
            "transfer_id": transfer_id,
            "chunk_index": i
        }, token=token)
        packet["payload_size"] = len(chunk_data)
        header_json = json.dumps(packet).encode('utf-8')
        srv_sock.sendall(struct.pack(">I", len(header_json)) + header_json + chunk_data)
        wait_for_packet(srv_sock, "CHUNK_ACK")
        
    # 9. Finish Upload
    send_packet(srv_sock, build_packet("UPLOAD_FINISH", {
        "transfer_id": transfer_id
    }, token=token))
    
    header, _ = wait_for_packet(srv_sock, "UPLOAD_SUCCESS")
    assert header["type"] == "UPLOAD_SUCCESS", f"Upload failed: {header}"
    print("Upload completed successfully after resume!")
    
    srv_sock.close()
    return server_host, server_port, token, sha256, gw_sock

def test_resume_download(server_host, server_port, token, original_sha256, gw_sock):
    print("\n--- Test Resume Download ---")
    srv_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv_sock.connect((server_host, server_port))
    send_packet(srv_sock, build_packet("AUTH_BACKEND", {"token": token}, token=token))
    wait_for_packet(srv_sock, "AUTH_BACKEND_OK")
    send_packet(srv_sock, build_packet("JOIN_ROOM_BACKEND", {"room_name": "General"}, token=token))
    wait_for_packet(srv_sock, "JOIN_ROOM_OK")
    
    # 1. Get File List
    send_packet(srv_sock, build_packet("FILE_LIST_REQUEST", {
        "room_name": "General"
    }, token=token))
    header, _ = wait_for_packet(srv_sock, "FILE_LIST_RESPONSE")
    files = header["payload"]["files"]
    target_file = max((f for f in files if f["original_filename"] == "test_resume.bin"), key=lambda x: x["file_id"])
    file_id = target_file["file_id"]
    
    # 2. Initiate Download
    send_packet(srv_sock, build_packet("DOWNLOAD_REQUEST", {
        "file_id": file_id
    }, token=token))
    
    header, _ = wait_for_packet(srv_sock, "DOWNLOAD_READY")
    transfer_id = header["payload"]["transfer_id"]
    total_chunks = header["payload"]["total_chunks"]
    print(f"Download initiated. Transfer ID: {transfer_id}, Total chunks: {total_chunks}")
    
    # 3. Receive half of chunks
    half_chunks = total_chunks // 2
    received_data = bytearray()
    
    for i in range(half_chunks):
        chunk_header, chunk_payload = wait_for_packet(srv_sock, "DOWNLOAD_CHUNK")
        assert chunk_header["type"] == "DOWNLOAD_CHUNK"
        received_data.extend(chunk_payload)
        
    print(f"Received {half_chunks} chunks. Disconnecting...")
    srv_sock.close()
    
    # 4. Reconnect
    srv_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv_sock.connect((server_host, server_port))
    send_packet(srv_sock, build_packet("AUTH_BACKEND", {"token": token}, token=token))
    wait_for_packet(srv_sock, "AUTH_BACKEND_OK")
    send_packet(srv_sock, build_packet("JOIN_ROOM_BACKEND", {"room_name": "General"}, token=token))
    wait_for_packet(srv_sock, "JOIN_ROOM_OK")
    
    # 5. Resume Download
    send_packet(srv_sock, build_packet("RESUME_TRANSFER", {
        "transfer_id": transfer_id,
        "direction": "download",
        "start_chunk": half_chunks
    }, token=token))
    
    header, _ = wait_for_packet(srv_sock, "DOWNLOAD_READY")
    assert header["type"] == "DOWNLOAD_READY"
    print(f"Resuming download from chunk {half_chunks}")
    
    # 6. Receive remaining chunks
    for i in range(half_chunks, total_chunks):
        chunk_header, chunk_payload = wait_for_packet(srv_sock, "DOWNLOAD_CHUNK")
        assert chunk_header["type"] == "DOWNLOAD_CHUNK"
        received_data.extend(chunk_payload)
        
    # Verify checksum
    calc_sha256 = hashlib.sha256(received_data).hexdigest()
    assert calc_sha256 == original_sha256, "Checksum mismatch after resumed download!"
    print("Download completed and checksum verified successfully after resume!")
    gw_sock.close()

if __name__ == "__main__":
    host, port, token, sha256, gw_sock = test_resume_upload()
    test_resume_download(host, port, token, sha256, gw_sock)
    print("\nAll resume transfer tests passed!")
