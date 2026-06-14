import socket
import threading
import time
import json
import argparse
import statistics
from common.protocol import send_packet, receive_packet, build_packet
from common.constants import DEFAULT_GATEWAY_HOST, DEFAULT_GATEWAY_CLIENT_PORT

class TestClient:
    def __init__(self, username, password):
        self.username = username
        self.password = password
        self.token = None
        self.sock = None
        self.latencies = []

    def connect_and_login(self):
        start = time.time()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((DEFAULT_GATEWAY_HOST, DEFAULT_GATEWAY_CLIENT_PORT))
        
        # 1. Try Login
        req = build_packet("LOGIN", {"username": self.username, "password": self.password})
        send_packet(self.sock, req)
        header, _ = receive_packet(self.sock)
        
        if header["type"] == "LOGIN_OK":
            self.token = header["token"]
            self.latencies.append(time.time() - start)
            return True
        
        # 2. Try Register if login failed (maybe user doesn't exist)
        req_reg = build_packet("REGISTER", {"username": self.username, "password": self.password})
        send_packet(self.sock, req_reg)
        header, _ = receive_packet(self.sock)
        
        if header["type"] == "REGISTER_OK":
            # Now login again
            send_packet(self.sock, req)
            header, _ = receive_packet(self.sock)
            if header["type"] == "LOGIN_OK":
                self.token = header["token"]
                self.latencies.append(time.time() - start)
                return True
                
        return False

    def send_ping(self):
        start = time.time()
        req = build_packet("PING", token=self.token)
        send_packet(self.sock, req)
        receive_packet(self.sock)
        self.latencies.append(time.time() - start)

    def close(self):
        if self.sock:
            self.sock.close()

def run_test(num_clients, iterations):
    clients = []
    print(f"[*] Starting Load Test with {num_clients} clients...")
    
    # 1. Setup/Login
    for i in range(num_clients):
        username = f"loadtest_user_{i}"
        c = TestClient(username, "pass123")
        if c.connect_and_login():
            clients.append(c)
        else:
            print(f"[!] Client {i} ({username}) failed to login/register")

    print(f"[+] {len(clients)} clients logged in.")

    # 2. Stress Test (Pings)
    for _ in range(iterations):
        for c in clients:
            try:
                c.send_ping()
            except:
                pass
        time.sleep(0.1)

    # 3. Collect Stats
    all_latencies = []
    for c in clients:
        all_latencies.extend(c.latencies)
        c.close()

    if all_latencies:
        print("\n--- Load Test Results ---")
        print(f"Total Requests: {len(all_latencies)}")
        print(f"Avg Latency:    {statistics.mean(all_latencies)*1000:.2f} ms")
        print(f"Min Latency:    {min(all_latencies)*1000:.2f} ms")
        print(f"Max Latency:    {max(all_latencies)*1000:.2f} ms")
        print(f"95th Percentile: {statistics.quantiles(all_latencies, n=20)[18]*1000:.2f} ms")
    else:
        print("[!] No latency data collected.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--clients", type=int, default=10)
    parser.add_argument("--rounds", type=int, default=5)
    args = parser.parse_args()
    run_test(args.clients, args.rounds)
