"""Shared packet helpers for the NetCourier TCP protocol."""

import json
import socket
import struct
from datetime import datetime
from itertools import count
from typing import Any

from common.constants import (
    EVENT_ID_PREFIX, 
    REQUEST_ID_PREFIX, 
    TIMESTAMP_FORMAT,
    HEADER_LENGTH_BYTES
)
from common.errors import ERROR_MESSAGES


_request_counter = count(1)
_event_counter = count(1)


def current_timestamp() -> str:
    """Return protocol timestamp in local time."""
    return datetime.now().strftime(TIMESTAMP_FORMAT)


def generate_request_id(prefix: str = REQUEST_ID_PREFIX) -> str:
    """Generate a traceable request id for protocol packets."""
    return f"{prefix}-{next(_request_counter):06d}"


def generate_event_id() -> str:
    """Generate an event id for server-pushed packets."""
    return f"{EVENT_ID_PREFIX}-{next(_event_counter):06d}"


def build_packet(
    message_type: str,
    payload: dict[str, Any] | None = None,
    *,
    request_id: str | None = None,
    token: str | None = None,
    payload_size: int = 0,
) -> dict[str, Any]:
    """Build a protocol header dictionary."""
    return {
        "type": message_type,
        "request_id": request_id or generate_request_id(),
        "token": token,
        "timestamp": current_timestamp(),
        "payload_size": payload_size,
        "payload": payload or {},
    }


def build_error_packet(
    code: str,
    *,
    message: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Build a protocol-compliant ERROR packet."""
    return build_packet(
        "ERROR",
        {
            "code": code,
            "message": message or ERROR_MESSAGES.get(code, code),
        },
        request_id=request_id,
    )


def send_packet(sock: socket.socket, header: dict[str, Any], payload: bytes = b""):
    """
    Send a length-prefixed packet over TCP.
    Format: [4 bytes header_length][JSON_HEADER][BINARY_PAYLOAD]
    """
    header["payload_size"] = len(payload)
    header_json = json.dumps(header).encode("utf-8")
    header_len = len(header_json)
    
    # Pack header length as 4 bytes big-endian
    full_packet = struct.pack(">I", header_len) + header_json + payload
    sock.sendall(full_packet)


def receive_packet(sock: socket.socket) -> tuple[dict[str, Any], bytes]:
    """
    Receive a length-prefixed packet from TCP.
    Returns (header_dict, binary_payload).
    """
    try:
        # Read header length (4 bytes)
        header_len_data = _recv_all(sock, HEADER_LENGTH_BYTES)
        if not header_len_data:
            raise ConnectionError("Socket closed while reading header length")
        
        header_len = struct.unpack(">I", header_len_data)[0]
        
        # Security: Sanity check for header length (e.g., max 64KB)
        if header_len > 65535:
            raise ValueError(f"Packet header too large: {header_len} bytes")
        
        # Read JSON header
        header_json_data = _recv_all(sock, header_len)
        if not header_json_data:
            raise ConnectionError("Socket closed while reading header JSON")
        
        try:
            header = json.loads(header_json_data.decode("utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON header: {e}")
            
        if not isinstance(header, dict):
            raise ValueError("Header must be a JSON object")
            
        # Read binary payload if any
        payload_size = header.get("payload_size", 0)
        
        # Security: Sanity check for payload size (e.g., max 20MB for file chunks)
        # 20MB = 20 * 1024 * 1024 = 20971520 bytes
        if payload_size > 20971520:
             raise ValueError(f"Packet payload too large: {payload_size} bytes")
             
        payload = b""
        if payload_size > 0:
            payload = _recv_all(sock, payload_size)
            if not payload:
                raise ConnectionError("Socket closed while reading binary payload")
                
        return header, payload
        
    except (struct.error, UnicodeDecodeError, ValueError) as e:
        # Re-wrap as a specific protocol error or just propagate for higher-level handling
        raise ValueError(f"Protocol violation: {e}")


def _recv_all(sock: socket.socket, n: int) -> bytes:
    """Helper to receive exactly n bytes."""
    data = bytearray()
    while len(data) < n:
        packet = sock.recv(n - len(data))
        if not packet:
            return b""
        data.extend(packet)
    return bytes(data)
