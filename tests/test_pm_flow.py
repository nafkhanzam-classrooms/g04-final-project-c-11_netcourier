import socket
import json
import struct
import time
import threading

def send_msg(s, type_, payload=None, token=None):
    msg = {
        "type": type_,
        "request_id": "req-" + str(time.time()),
        "timestamp": "2026-06-09 20:00:00",
        "payload_size": 0,
        "payload": payload or {}
    }
    if token:
        msg["token"] = token
    header = json.dumps(msg).encode('utf-8')
    s.sendall(struct.pack('!I', len(header)) + header)

def recv_msg(s):
    try:
        length_bytes = s.recv(4)
        if not length_bytes:
            return None
        length = struct.unpack('!I', length_bytes)[0]
        header_bytes = s.recv(length)
        return json.loads(header_bytes.decode('utf-8'))
    except Exception as e:
        print("Recv err", e)
        return None

def test_pm():
    s1 = socket.socket()
    s1.connect(('127.0.0.1', 9000))
    send_msg(s1, "REGISTER", {"username": "test1", "password": "123"})
    print("Reg1", recv_msg(s1))
    send_msg(s1, "LOGIN", {"username": "test1", "password": "123"})
    login1 = recv_msg(s1)
    print("Login1", login1)
    token1 = login1.get("token")

    s2 = socket.socket()
    s2.connect(('127.0.0.1', 9000))
    send_msg(s2, "REGISTER", {"username": "test2", "password": "123"})
    print("Reg2", recv_msg(s2))
    send_msg(s2, "LOGIN", {"username": "test2", "password": "123"})
    login2 = recv_msg(s2)
    print("Login2", login2)
    token2 = login2.get("token")

    # test1 sends to test2
    send_msg(s1, "PRIVATE_MESSAGE_SEND", {"recipient_username": "test2", "content": "hello from test1"}, token1)
    print("s1 sent PM")
    print("s1 recv status", recv_msg(s1))
    print("s2 recv pm", recv_msg(s2))

    # s1 offline PM to test3
    send_msg(s1, "REGISTER", {"username": "test3", "password": "123"})
    recv_msg(s1)
    send_msg(s1, "PRIVATE_MESSAGE_SEND", {"recipient_username": "test3", "content": "offline msg"}, token1)
    print("s1 sent offline PM")
    print("s1 recv status", recv_msg(s1))

    # test1 requests history
    send_msg(s1, "PM_HISTORY_REQUEST", {"other_username": "test2"}, token1)
    print("s1 history", recv_msg(s1))

    # list users
    send_msg(s1, "LIST_ONLINE_USERS", {}, token1)
    print("online users", recv_msg(s1))

    s1.close()
    s2.close()

if __name__ == "__main__":
    test_pm()
