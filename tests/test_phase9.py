import socket
import json
import struct
import time
from common.constants import DEFAULT_GATEWAY_HOST, DEFAULT_GATEWAY_CLIENT_PORT

def test_malformed_header():
    print("[*] Testing malformed header...")
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((DEFAULT_GATEWAY_HOST, DEFAULT_GATEWAY_CLIENT_PORT))
        
        # Send huge header length
        sock.sendall(struct.pack(">I", 1000000))
        
        # Should be disconnected or receive error
        data = sock.recv(1024)
        print(f"[+] Received: {data}")
    except Exception as e:
        print(f"[+] Error (expected): {e}")
    finally:
        sock.close()

def test_invalid_json():
    print("\n[*] Testing invalid JSON...")
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((DEFAULT_GATEWAY_HOST, DEFAULT_GATEWAY_CLIENT_PORT))
        
        header_raw = b"{invalid_json"
        sock.sendall(struct.pack(">I", len(header_raw)) + header_raw)
        
        data = sock.recv(1024)
        print(f"[+] Received: {data}")
    except Exception as e:
        print(f"[+] Error: {e}")
    finally:
        sock.close()

def test_large_payload():
    print("\n[*] Testing large payload...")
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((DEFAULT_GATEWAY_HOST, DEFAULT_GATEWAY_CLIENT_PORT))
        
        header = {
            "type": "PING",
            "request_id": "test-1",
            "payload_size": 30000000 # 30MB, exceeds 20MB limit
        }
        header_json = json.dumps(header).encode('utf-8')
        sock.sendall(struct.pack(">I", len(header_json)) + header_json)
        
        data = sock.recv(1024)
        print(f"[+] Received: {data}")
    except Exception as e:
        print(f"[+] Error: {e}")
    finally:
        sock.close()

def test_token_expiry():
    print("\n[*] Testing token expiry optional...")
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((DEFAULT_GATEWAY_HOST, DEFAULT_GATEWAY_CLIENT_PORT))
        
        # 1. Login with a short expiry
        username = f"user_{int(time.time())}"
        
        # First register
        from common.protocol import build_packet, receive_packet, send_packet
        reg_pkt = build_packet("REGISTER", {
            "username": username,
            "password": "password123",
            "display_name": "Test User"
        }, request_id="reg-1")
        send_packet(sock, reg_pkt)
        receive_packet(sock) # REGISTER_OK
        
        # Then login with expires_in = 1 second
        login_pkt = build_packet("LOGIN", {
            "username": username,
            "password": "password123",
            "expires_in": 1 # expires in 1 second
        }, request_id="log-1")
        send_packet(sock, login_pkt)
        
        res, _ = receive_packet(sock)
        if res.get("type") != "LOGIN_OK":
            print(f"[!] Login failed: {res}")
            return
            
        token = res.get("token")
        print(f"[+] Logged in, token: {token}")
        
        # 2. Wait for it to expire
        print("[*] Waiting for 2 seconds...")
        time.sleep(2)
        
        # 3. Try to use the token
        ping_pkt = build_packet("PING", {}, request_id="ping-1", token=token)
        send_packet(sock, ping_pkt)
        
        res, _ = receive_packet(sock)
        print(f"[+] Received after expiry: {res}")
        if res.get("type") == "ERROR" and res.get("payload", {}).get("code") == "EXPIRED_TOKEN":
            print("[+] Token expiry working correctly.")
        else:
            print("[!] Token did not expire as expected.")
    except Exception as e:
        print(f"[+] Error: {e}")
    finally:
        sock.close()

if __name__ == "__main__":
    # Note: These tests require the Gateway to be running
    try:
        test_malformed_header()
        test_invalid_json()
        test_large_payload()
        test_token_expiry()
    except ConnectionRefusedError:
        print("[!] Gateway not running. Please start it first.")
