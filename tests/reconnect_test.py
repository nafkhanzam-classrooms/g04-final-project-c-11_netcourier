import socket
import time
import argparse
from common.protocol import send_packet, receive_packet, build_packet
from common.constants import DEFAULT_GATEWAY_HOST, DEFAULT_GATEWAY_CLIENT_PORT

def test_reconnect(username, password):
    print(f"[*] Testing Reconnection for {username}...")
    
    # 1. First Connection
    sock1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock1.connect((DEFAULT_GATEWAY_HOST, DEFAULT_GATEWAY_CLIENT_PORT))
    
    login_req = build_packet("LOGIN", {"username": username, "password": password})
    send_packet(sock1, login_req)
    header, _ = receive_packet(sock1)
    
    if header["type"] != "LOGIN_OK":
        print("[*] User might not exist, registering...")
        reg_req = build_packet("REGISTER", {"username": username, "password": password})
        send_packet(sock1, reg_req)
        receive_packet(sock1)
        send_packet(sock1, login_req)
        header, _ = receive_packet(sock1)

    if header["type"] == "LOGIN_OK":
        token = header["token"]
        print(f"[+] Login successful. Token: {token}")
    else:
        print("[!] Login failed.")
        return

    # 2. Simulate Disconnect (Close socket without logout)
    print("[*] Simulating unexpected disconnect...")
    sock1.close()
    time.sleep(2)
    
    # 3. Reconnect and Login again
    print("[*] Reconnecting...")
    sock2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock2.connect((DEFAULT_GATEWAY_HOST, DEFAULT_GATEWAY_CLIENT_PORT))
    
    send_packet(sock2, login_req)
    header, _ = receive_packet(sock2)
    
    if header["type"] == "LOGIN_OK":
        print(f"[+] Re-login successful. Gateway should have cleaned up old session.")
    else:
        print(f"[!] Re-login failed: {header}")

    sock2.close()
    print("[+] Reconnection test finished.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", default="admin")
    parser.add_argument("--pw", default="admin")
    args = parser.parse_args()
    test_reconnect(args.user, args.pw)
