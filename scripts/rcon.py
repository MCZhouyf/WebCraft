#!/usr/bin/env python3
import argparse
import socket
import struct


def recv_exact(sock, size):
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ConnectionError("RCON connection closed")
        data.extend(chunk)
    return bytes(data)


def recv_packet(sock):
    length = struct.unpack("<i", recv_exact(sock, 4))[0]
    payload = recv_exact(sock, length)
    request_id, packet_type = struct.unpack("<ii", payload[:8])
    body = payload[8:-2].decode("utf-8", errors="replace")
    return request_id, packet_type, body


def send_packet(sock, request_id, packet_type, body):
    payload = struct.pack("<ii", request_id, packet_type) + body.encode() + b"\x00\x00"
    sock.sendall(struct.pack("<i", len(payload)) + payload)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("command", nargs="+", help="RCON command")
    args = parser.parse_args()

    with socket.create_connection(("127.0.0.1", args.port), timeout=10) as sock:
        send_packet(sock, 1, 3, args.password)
        request_id, _, _ = recv_packet(sock)
        if request_id == -1:
            raise SystemExit("RCON authentication failed")
        send_packet(sock, 2, 2, " ".join(args.command))
        _, _, body = recv_packet(sock)
        print(body)


if __name__ == "__main__":
    main()
