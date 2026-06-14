
import socket
import json
import struct
import time

def send_packet(sock, packet):
    header_json = json.dumps(packet).encode('utf-8')
    payload_size = packet.get("payload_size", 0)
    sock.sendall(struct.pack(">I", len(header_json)) + header_json)

def receive_packet(sock):
    data = sock.recv(4)
    if not data: return None, None
    header_len = struct.unpack(">I", data)[0]
    header_json = sock.recv(header_len).decode('utf-8')
    header = json.loads(header_json)
    payload_size = header.get("payload_size", 0)
    payload = b""
    if payload_size > 0:
        while len(payload) < payload_size:
            chunk = sock.recv(min(payload_size - len(payload), 4096))
            if not chunk: break
            payload += chunk
    return header, payload

def test_reaction():
    host = "127.0.0.1"
    gateway_port = 9000
    
    print("[*] Connecting to Gateway...")
    gw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    gw.connect((host, gateway_port))
    
    # Login
    print("[*] Logging in...")
    send_packet(gw, {
        "type": "LOGIN",
        "request_id": "req-1",
        "payload": {"username": "debuguser", "password": "password123"}
    })
    header, _ = receive_packet(gw)
    
    token = header.get("token")
    if not token:
        print(f"[!] Login failed, no token. Header: {header}")
        return
    print(f"[+] Logged in, token: {token}")
    
    # Join Room
    print("[*] Joining room General...")
    send_packet(gw, {
        "type": "JOIN_ROOM",
        "token": token,
        "payload": {"room_name": "General"}
    })
    header, _ = receive_packet(gw)
    if header["type"] == "ERROR":
        print(f"[!] Join Room failed: {header['payload'].get('message')}")
        return
    loc = header["payload"]
    
    # Connect to Process Server
    print(f"[*] Connecting to Process Server {loc['server_id']} at {loc['port']}...")
    ps = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ps.connect((host, loc["port"]))
    
    # Auth at PS
    send_packet(ps, {
        "type": "AUTH_BACKEND",
        "token": token,
        "payload": {}
    })
    receive_packet(ps)
    
    # Join at PS
    send_packet(ps, {
        "type": "JOIN_ROOM_BACKEND",
        "payload": {"room_name": "General"}
    })
    receive_packet(ps)
    
    # Send Message
    print("[*] Sending message to get a message_id...")
    send_packet(ps, {
        "type": "ROOM_CHAT_SEND",
        "payload": {"room_name": "General", "message": "Debug reaction " + str(time.time())}
    })
    
    msg_id = None
    # Wait for broadcast
    for _ in range(5):
        header, _ = receive_packet(ps)
        if header and header.get("type") == "ROOM_CHAT_BROADCAST":
            print(f"[*] Received Broadcast: {header}")
            msg_id = header["payload"].get("message_id")
            break
        time.sleep(0.5)

    print(f"[+] Message sent, ID: {msg_id}")
    
    if not msg_id:
        print("[!] Error: No message_id received!")
        return

    # React
    print(f"[*] Attempting to react to message {msg_id} with 👍...")
    send_packet(ps, {
        "type": "ROOM_MESSAGE_REACTION",
        "payload": {
            "message_id": msg_id,
            "emoji": "👍",
            "action": "add"
        }
    })
    
    header, _ = receive_packet(ps)
    print(f"[*] Server Response: {header}")
    
    if header["type"] == "ERROR":
        print(f"[!] REACTION FAILED: {header['payload'].get('message')}")
    else:
        print("[+] REACTION SUCCESSFUL!")

if __name__ == "__main__":
    test_reaction()
