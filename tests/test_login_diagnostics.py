
import socket
import json
import struct
import time
import sys

def send_packet(sock, packet):
    header_json = json.dumps(packet).encode('utf-8')
    sock.sendall(struct.pack(">I", len(header_json)) + header_json)

def receive_packet(sock):
    data = sock.recv(4)
    if not data: return None
    header_len = struct.unpack(">I", data)[0]
    header_json = sock.recv(header_len).decode('utf-8')
    return json.loads(header_json)

def run_test():
    host = "127.0.0.1"
    port = 9000
    print(f"[*] Attempting to connect to Gateway at {host}:{port}...")
    
    start_time = time.time()
    try:
        with socket.create_connection((host, port), timeout=5) as sock:
            conn_time = time.time() - start_time
            print(f"[+] Connected in {conn_time:.4f}s")
            
            # Try LOGIN
            print("[*] Sending LOGIN request for 'debuguser'...")
            send_packet(sock, {
                "type": "LOGIN",
                "request_id": "diag-1",
                "payload": {"username": "debuguser", "password": "password123"}
            })
            
            resp = receive_packet(sock)
            total_time = time.time() - start_time
            print(f"[+] Received response in {total_time:.4f}s")
            print(f"[*] Response Type: {resp.get('type')}")
            
            if resp.get("type") == "LOGIN_OK":
                print("[SUCCESS] Login protocol is working correctly.")
            else:
                print(f"[FAILURE] Server returned: {resp}")
                
    except socket.timeout:
        print("[ERROR] Connection timed out. Gateway might be hanging.")
    except Exception as e:
        print(f"[ERROR] Connection failed: {e}")

if __name__ == "__main__":
    run_test()
