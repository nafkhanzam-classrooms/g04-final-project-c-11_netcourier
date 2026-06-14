"""Protocol error codes and helpers."""

from __future__ import annotations

from dataclasses import dataclass


INVALID_PACKET = "INVALID_PACKET"
INVALID_JSON = "INVALID_JSON"
MISSING_FIELD = "MISSING_FIELD"
INVALID_TOKEN = "INVALID_TOKEN"
EXPIRED_TOKEN = "EXPIRED_TOKEN"
INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
DUPLICATE_LOGIN = "DUPLICATE_LOGIN"
USERNAME_TAKEN = "USERNAME_TAKEN"
USER_NOT_FOUND = "USER_NOT_FOUND"
ROOM_NOT_FOUND = "ROOM_NOT_FOUND"
ROOM_ALREADY_EXISTS = "ROOM_ALREADY_EXISTS"
NOT_IN_ROOM = "NOT_IN_ROOM"
RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
FILE_TOO_LARGE = "FILE_TOO_LARGE"
FILE_NOT_FOUND = "FILE_NOT_FOUND"
CHECKSUM_FAILED = "CHECKSUM_FAILED"
TRANSFER_TIMEOUT = "TRANSFER_TIMEOUT"
BACKEND_DOWN = "BACKEND_DOWN"
INTERNAL_ERROR = "INTERNAL_ERROR"


ERROR_MESSAGES = {
    INVALID_PACKET: "Packet format is invalid.",
    INVALID_JSON: "Packet header is not valid JSON.",
    MISSING_FIELD: "A required field is missing.",
    INVALID_TOKEN: "Session token is invalid.",
    EXPIRED_TOKEN: "Session token has expired.",
    INVALID_CREDENTIALS: "Username or password is incorrect.",
    DUPLICATE_LOGIN: "User is already logged in.",
    USERNAME_TAKEN: "Username is already taken.",
    USER_NOT_FOUND: "User was not found.",
    ROOM_NOT_FOUND: "Room was not found.",
    ROOM_ALREADY_EXISTS: "Room already exists.",
    NOT_IN_ROOM: "User has not joined the room.",
    RATE_LIMIT_EXCEEDED: "Too many requests.",
    FILE_TOO_LARGE: "File exceeds the configured size limit.",
    FILE_NOT_FOUND: "File was not found.",
    CHECKSUM_FAILED: "Checksum validation failed.",
    TRANSFER_TIMEOUT: "File transfer timed out.",
    BACKEND_DOWN: "Backend server is down.",
    INTERNAL_ERROR: "Internal server error.",
}


@dataclass(frozen=True)
class ProtocolError(Exception):
    """Application-level protocol error."""

    code: str
    message: str | None = None

    def __str__(self) -> str:
        return self.message or ERROR_MESSAGES.get(self.code, self.code)
