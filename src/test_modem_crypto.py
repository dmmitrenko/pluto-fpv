"""Run: radioconda/python.exe -m unittest discover -s src -p test_modem_crypto.py"""
import os
import time
import unittest
from pathlib import Path
import numpy as np
import yaml
from modem_crypto import FrameCipher, CRYPTO_OVERHEAD, FRAME_OVERHEAD
from fpv_tx_packetizer import blk as Tx
from fpv_rx_depacketizer import blk as Rx


def ts(n=4):
    return b"".join(b"\x47" + os.urandom(187) for _ in range(n))


class Sink:
    def __init__(self): self.data = []
    def sendto(self, data, address): self.data.append(data)


class ModemCryptoTests(unittest.TestCase):
    def test_counter_ranges_restart_and_wrong_key(self):
        key = os.urandom(16)
        tx = FrameCipher(key); rx = FrameCipher(key)
        payload = ts()
        first = tx.encrypt(payload); second = tx.encrypt(payload)
        self.assertEqual(len(first), len(payload) + CRYPTO_OVERHEAD)
        self.assertNotEqual(first, second)
        self.assertEqual(int.from_bytes(second[12:16], "big"), (len(payload)+4+15)//16)
        self.assertEqual(rx.decrypt(second), payload)  # previous record can be lost
        self.assertEqual(rx.decrypt(first), payload)
        restarted = FrameCipher(key).encrypt(payload)
        self.assertNotEqual(restarted[:12], first[:12])
        self.assertEqual(rx.decrypt(restarted), payload)
        with self.assertRaises(ValueError): FrameCipher(os.urandom(16)).decrypt(first)
        tx.counter = 2**32 - 1
        with self.assertRaises(OverflowError): tx.encrypt(payload)

    def frame(self, tx, payload):
        tx._queue.extend(payload); tx._queue_time = time.monotonic()-1
        return tx._next_frame()

    def test_real_modem_loss_corruption_idle_restart(self):
        tx = Tx(); rx = Rx(); sink = Sink(); rx._sock = sink
        originals = [ts() for _ in range(8)]
        frames = [self.frame(tx, x) for x in originals]
        self.assertEqual(len(frames[0]), 752+FRAME_OVERHEAD)
        damaged = bytearray(frames[3]); damaged[40] ^= 1
        idle = tx._next_frame()
        # Lose frame 2, corrupt frame 3, cross arbitrary scheduler boundaries.
        wire = frames[0]+frames[1]+bytes(damaged)+b"".join(frames[4:])+idle
        bits = np.unpackbits(np.frombuffer(wire, dtype=np.uint8))
        for i in range(0,len(bits),379): rx.work([bits[i:i+379]], [])
        self.assertEqual(sink.data, originals[:2]+originals[4:])
        self.assertGreater(rx._total_crc_errors, 0)
        self.assertEqual(rx._total_lost, 2)
        fresh = Tx(); payload=ts(); wire=self.frame(fresh,payload)
        rx.work([np.unpackbits(np.frombuffer(wire,dtype=np.uint8))], [])
        self.assertEqual(sink.data[-1], payload)

    def test_short_payload_padding_and_sequence_wrap(self):
        tx=Tx(); rx=Rx(); tx._seq=65535
        for count in [1,4]:
            payload=ts(count); frame=self.frame(tx,payload)
            body=rx._decode(np.unpackbits(np.frombuffer(frame,dtype=np.uint8)),True)
            self.assertIsNotNone(body)
            self.assertEqual(body[3:3+len(payload)],payload)
            if count==1: self.assertEqual(len(body[3:-4]),752)
        self.assertEqual(tx._seq,1)

    def test_grc_sources_and_timing(self):
        root=Path(__file__).parent
        for filename,block,source in [('fpv_tx.grc','packetizer','fpv_tx_packetizer.py'),
                ('fpv_tx_headless.grc','packetizer','fpv_tx_packetizer.py'),
                ('fpv_rx.grc','depacketizer','fpv_rx_depacketizer.py')]:
            doc=yaml.safe_load((root/filename).read_text())
            code=next(b for b in doc['blocks'] if b['name']==block)['parameters']['_source_code']
            self.assertEqual(code,(root/source).read_text())
            compile(code,filename,'exec')
            if block=='packetizer':
                formula=next(b for b in doc['blocks'] if b['name']=='samples_per_frame')['parameters']['value']
                self.assertEqual(eval(formula,{},dict(ts_per_frame=4,fec_enabled=1,sps=2)),(752+35)*16)


if __name__ == '__main__': unittest.main()
