"""AES-128/256-GCM records carried in opaque 188-byte TS packets (PID 0x1FFE).

The modem must preserve non-null TS packets, including their payload bytes.
UDP grouping may change; missing fragments discard only their own record.
"""
import argparse
from collections import OrderedDict
from pathlib import Path
import os
import socket
import struct
import threading
import time

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"VAG1"
HEADER = struct.Struct("!4s12sBBH")
CHUNK = 184 - HEADER.size
MAX_PLAIN = 7 * 188


def load_key(filename, bits=128):
    key = Path(filename).read_bytes()
    if bits not in (128, 256) or len(key) != bits // 8:
        raise ValueError("AES-%d key file must contain exactly %d raw bytes" % (bits, bits // 8))
    return key


class Encoder:
    def __init__(self, key):
        self.aes = AESGCM(key)
        self.cc = 0
        self.records = 0

    def encode(self, data):
        if not data or len(data) > MAX_PLAIN or len(data) % 188:
            raise ValueError("Expected 1..7 complete TS packets")
        if any(data[i] != 0x47 for i in range(0, len(data), 188)):
            raise ValueError("Invalid TS sync byte")
        # Random 96-bit nonce; stop well before the per-key 2**32 record limit.
        if self.records >= 2**24:
            raise ValueError("Record limit reached; provision a new key")
        nonce = os.urandom(12)
        encrypted = self.aes.encrypt(nonce, data, MAGIC)
        self.records += 1
        count = (len(encrypted) + CHUNK - 1) // CHUNK
        packets = []
        for index in range(count):
            body = HEADER.pack(MAGIC, nonce, index, count, len(encrypted))
            body += encrypted[index * CHUNK:(index + 1) * CHUNK]
            packets.append(bytes((0x47, 0x1F, 0xFE, 0x10 | self.cc))
                           + body.ljust(184, b"\xff"))
            self.cc = (self.cc + 1) % 16
        return packets


class Decoder:
    def __init__(self, key):
        self.aes = AESGCM(key)
        self.pending = OrderedDict()
        self.seen = OrderedDict()
        self.rejected = 0

    def feed(self, data):
        output = bytearray()
        for offset in range(0, len(data) - 187, 188):
            packet = data[offset:offset + 188]
            if packet[:3] != b"\x47\x1f\xfe" or packet[3] & 0xF0 != 0x10:
                continue
            magic, nonce, index, count, size = HEADER.unpack(packet[4:24])
            if (magic != MAGIC or size < 204 or size > MAX_PLAIN + 16
                    or (size - 16) % 188 or count != (size + CHUNK - 1) // CHUNK
                    or index >= count or nonce in self.seen):
                continue
            identity = (nonce, size)
            parts = self.pending.setdefault(identity, {})
            parts[index] = packet[24:24 + min(CHUNK, size - index * CHUNK)]
            if len(self.pending) > 128:
                self.pending.popitem(last=False)
            if len(parts) != count:
                continue
            del self.pending[identity]
            try:
                plain = self.aes.decrypt(nonce, b"".join(parts[i] for i in range(count)), MAGIC)
            except InvalidTag:
                self.rejected += 1
                continue
            self.seen[nonce] = None
            if len(self.seen) > 4096:
                self.seen.popitem(last=False)
            output.extend(plain)
        return bytes(output)


class FrameEncoder:
    """One authenticated record per modem payload; retain the VAG1 wire format."""
    def __init__(self, key, ts_per_frame=4):
        self.frame_packets = int(ts_per_frame)
        # Keep records readable by existing VAG1 receivers (at most 7 input TS).
        if not 2 <= self.frame_packets <= 9:
            raise ValueError("Encrypted transport requires ts_per_frame between 2 and 9")
        self.plain_packets = min(7, (self.frame_packets * CHUNK - 16) // 188)
        self.plain_bytes = self.plain_packets * 188
        self.encoder = Encoder(key)

    def encode(self, data):
        if len(data) > self.plain_bytes:
            raise ValueError("Record does not fit one modem frame")
        packets = self.encoder.encode(data)
        null = b"\x47\x1f\xff\x10" + b"\xff" * 184
        return b"".join(packets) + null * (self.frame_packets - len(packets))


class EncryptRelay(threading.Thread):
    def __init__(self, key, target_port, ts_per_frame=4, wait_ms=2):
        super().__init__(daemon=True)
        self.wait_s = float(wait_ms) / 1000
        if not 0 < self.wait_s <= 0.1:
            raise ValueError("crypto_wait_ms must be greater than 0 and at most 100")
        self.encoder = FrameEncoder(key, ts_per_frame)
        self.target = ("127.0.0.1", target_port)
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.listener.bind(("127.0.0.1", 0))
        self.port = self.listener.getsockname()[1]
        self.listener.settimeout(self.wait_s)
        self.sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.stopping = threading.Event()
        self.error = None

    def run(self):
        pending = bytearray()
        deadline = None
        try:
            while not self.stopping.is_set():
                remaining = max(0.0001, deadline - time.monotonic()) if pending else self.wait_s
                self.listener.settimeout(remaining)
                try:
                    data = self.listener.recv(65536)
                except socket.timeout:
                    data = b""
                if data:
                    if len(data) % 188 or any(data[i] != 0x47 for i in range(0, len(data), 188)):
                        raise ValueError("Expected complete MPEG-TS packets from ffmpeg")
                    if not pending:
                        deadline = time.monotonic() + self.wait_s
                    pending.extend(data)
                while len(pending) >= self.encoder.plain_bytes:
                    chunk = bytes(pending[:self.encoder.plain_bytes])
                    del pending[:self.encoder.plain_bytes]
                    self.sender.sendto(self.encoder.encode(chunk), self.target)
                # Bound latency of a short final record; padding keeps frame alignment.
                if pending and time.monotonic() >= deadline:
                    self.sender.sendto(self.encoder.encode(bytes(pending)), self.target)
                    pending.clear()
        except Exception as exc:
            self.error = exc
        finally:
            self.listener.close()
            self.sender.close()

    def close(self):
        self.stopping.set()
        self.join()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create a shared AES-GCM binary key")
    parser.add_argument("key_file")
    parser.add_argument("--aes-bits", type=int, choices=(128, 256), default=128)
    args = parser.parse_args()
    with open(args.key_file, "xb") as stream:
        stream.write(AESGCM.generate_key(bit_length=args.aes_bits))
    print("Key created. Copy securely to RX; do not publish it.")
