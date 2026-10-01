"""AES-128-CTR adapted from the supplied OFDM TX/RX embedded blocks.

Runs inside GNU Radio packetizer/depacketizer, not in the video applications.
CRC detects accidental damage/wrong keys; CTR is NOT authenticated encryption.
No replay protection. Both endpoints must use the same embedded key.
"""
import os
import zlib
from Crypto.Cipher import AES

# Shared secret: keep this module private. Change on both endpoints together.
KEY = bytes.fromhex("14ddcf63d5b3cc389d4017aee0e9c562")
CRYPTO_OVERHEAD = 16 + 4  # transmitted counter block + encrypted payload CRC32
FRAME_OVERHEAD = 15 + CRYPTO_OVERHEAD


class FrameCipher:
    def __init__(self, key=KEY):
        if len(key) != 16:
            raise ValueError("AES-128 requires exactly 16 key bytes")
        self.key = key
        self.nonce = os.urandom(12)
        self.counter = 0

    def encrypt(self, payload):
        plain = payload + zlib.crc32(payload).to_bytes(4, "big")
        blocks = (len(plain) + 15) // 16
        if self.counter + blocks > 2**32:
            raise OverflowError("AES counter exhausted; restart TX for a fresh nonce")
        iv = self.nonce + self.counter.to_bytes(4, "big")
        cipher = AES.new(self.key, AES.MODE_CTR, nonce=self.nonce,
                         initial_value=self.counter)
        self.counter += blocks  # no overlapping counter ranges between frames
        return iv + cipher.encrypt(plain)

    def decrypt(self, record):
        if len(record) < CRYPTO_OVERHEAD:
            raise ValueError("Truncated AES frame")
        iv = record[:16]
        cipher = AES.new(self.key, AES.MODE_CTR, nonce=iv[:12],
                         initial_value=int.from_bytes(iv[12:], "big"))
        plain = cipher.decrypt(record[16:])
        if zlib.crc32(plain[:-4]).to_bytes(4, "big") != plain[-4:]:
            raise ValueError("AES plaintext CRC mismatch (wrong key or damaged frame)")
        return plain[:-4]
