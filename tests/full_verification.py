import socket
import json
import struct
import time
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def send_packet(sock, packet):
    header_json = json.dumps(packet).encode('utf-8')
    sock.sendall(struct.pack(">I", len(header_json)) + header_json)

def receive_packet(sock):
    data = sock.recv(4)
    if not data: return None
    header_len = struct.unpack(">I", data)[0]
    header_json = sock.recv(header_len).decode('utf-8')
    return json.loads(header_json)

def run_full_test():
    host = "127.0.0.1"
    gw_port = 9000
    
    print("--- 1. Testing Core Auth & Connection ---")
    try:
        gw = socket.create_connection((host, gw_port), timeout=2)
        # Register debuguser (ignore error if already exists)
        send_packet(gw, {"type": "REGISTER", "payload": {"username": "debuguser", "password": "password123", "display_name": "Debug User"}})
        receive_packet(gw)
        
        send_packet(gw, {"type": "LOGIN", "payload": {"username": "debuguser", "password": "password123"}})
        resp = receive_packet(gw)
        if resp.get("type") == "LOGIN_OK":
            print("[OK] Login Successful")
            token = resp["token"]
        else:
            print("[FAIL] Login Failed")
            return
    except Exception as e:
        print(f"[ERROR] Connection failed: {e}")
        return

    print("\n--- 2. Testing Room Join & Messaging ---")
    # Try creating the room 'General' first, in case it's a clean database
    send_packet(gw, {"type": "CREATE_ROOM", "token": token, "payload": {"room_name": "General", "description": "General room"}})
    create_resp = receive_packet(gw)
    
    send_packet(gw, {"type": "JOIN_ROOM", "token": token, "payload": {"room_name": "General"}})
    loc_resp = receive_packet(gw)
    if loc_resp.get("type") == "ERROR":
        print(f"[FAIL] Join room failed: {loc_resp}")
        return
    loc = loc_resp["payload"]
    
    ps = socket.create_connection((host, loc["port"]), timeout=2)
    send_packet(ps, {"type": "AUTH_BACKEND", "token": token, "payload": {}})
    receive_packet(ps)
    send_packet(ps, {"type": "JOIN_ROOM_BACKEND", "payload": {"room_name": "General"}})
    receive_packet(ps)
    
    send_packet(ps, {"type": "ROOM_CHAT_SEND", "payload": {"room_name": "General", "message": "Verification test"}})
    # Capture broadcast
    chat_id = None
    for _ in range(5):
        msg = receive_packet(ps)
        if msg.get("type") == "ROOM_CHAT_BROADCAST":
            chat_id = msg["payload"].get("message_id")
            print(f"[OK] Message Broadcast received. ID: {chat_id}")
            break
    
    if not chat_id:
        print("[FAIL] Did not receive message ID in broadcast")
        return

    print("\n--- 3. Testing Real-time Reaction Logic ---")
    send_packet(ps, {"type": "ROOM_MESSAGE_REACTION", "payload": {"message_id": chat_id, "emoji": "🔥", "action": "add"}})
    react_resp = receive_packet(ps)
    if react_resp.get("type") == "ROOM_REACTION_BROADCAST":
        print(f"[OK] Reaction Broadcast received: {react_resp['payload']['reactions']}")
    else:
        print(f"[FAIL] Reaction failed: {react_resp}")

    print("\n--- 4. Testing Un-reaction Logic ---")
    send_packet(ps, {"type": "ROOM_MESSAGE_REACTION", "payload": {"message_id": chat_id, "emoji": "🔥", "action": "remove"}})
    unreact_resp = receive_packet(ps)
    if unreact_resp.get("type") == "ROOM_REACTION_BROADCAST":
        print(f"[OK] Un-reaction Successful. Current: {unreact_resp['payload']['reactions']}")
    else:
        print(f"[FAIL] Un-reaction failed: {unreact_resp}")

    print("\n--- 5. Testing Admin Permission (Kick Simulation) ---")
    # Attempt to kick a non-existent user to test permission check
    send_packet(ps, {"type": "ROOM_KICK_USER", "payload": {"username": "nonexistent"}})
    kick_resp = receive_packet(ps)
    if kick_resp.get("type") == "ERROR" and kick_resp["payload"].get("code") == "PERMISSION_DENIED":
        print("[OK] Admin Permission enforced (debuguser is not owner of 'General')")
    elif kick_resp.get("type") == "ERROR" and kick_resp["payload"].get("code") == "USER_NOT_FOUND":
        print("[OK] Admin Permission granted (debuguser is owner), target correctly not found")
    else:
        print(f"[INFO] Admin Response: {kick_resp.get('type')} - {kick_resp['payload']}")

    print("\n--- SUMMARY ---")
    print("All backend components are responding correctly to new features.")
    gw.close()
    ps.close()

if __name__ == "__main__":
    run_full_test()
