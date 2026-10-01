"""Video side of the RECEIVER: fpv_rx -> UDP -> counters -> ffplay window.

Listens on udp_out_port ([main] of rx.ini), where fpv_rx.grc delivers the
encrypted TS envelopes, authenticates and decrypts them before ffplay, and counts:

    frame rate      starts of video frames in the stream
    bit rate        without the padding packets added by the modem
    lost packets    gaps in the MPEG-TS continuity counters
    stalls          no new video frame for longer than stall_ms
    decode errors   warnings and errors printed by the decoder of ffplay

Everything goes to video_rx.log; the totals are printed at the end.

Usage:
    python video_rx.py   run until Ctrl+C or until the window is closed
    python video_rx.py --no-player     counters only, no window
"""
import argparse
import socket
import subprocess
import threading
import time

import video_common
from video_crypto import Decoder, load_key

# Shared secret: anyone with this source file can extract the key.
EMBEDDED_AES_KEY = bytes.fromhex("075361aa0d6e6db640b40efd064a467c7921072d721d21ae4399216b9115c92c")

VIDEO_DEFAULTS = dict(player_port="5002", stall_ms="200", window_title="FPV RX",
                      log_file="video_rx.log", ffmpeg_dir="")
MAIN_DEFAULTS = dict(udp_out_port="5001")

TS_LEN = 188
# Messages of ffmpeg that are not problems (full-range colours of webcams).
HARMLESS = ("deprecated pixel format",)
NULL_PID = 0x1FFF
CONSOLE_PERIOD = 1.0
LOG_PERIOD = 10.0


class TsCounters:
    """Counts frames, losses and stalls from the MPEG-TS packets."""

    def __init__(self, stall_s, log):
        self.stall_s = stall_s
        self.log = log
        self.continuity = {}  # PID -> last continuity counter
        self.video_pid = None
        self.frames = self.lost = self.payload_bytes = 0
        self.stalls = 0
        self.longest_stall = 0.0
        self.stalled_time = 0.0
        self.last_frame_time = None
        self.first_frame_time = None
        self.in_stall = False

    def feed(self, data, now):
        for i in range(0, len(data) - TS_LEN + 1, TS_LEN):
            if data[i] != 0x47:
                continue
            pid = ((data[i + 1] & 0x1F) << 8) | data[i + 2]
            if pid == NULL_PID:
                continue
            self.payload_bytes += TS_LEN
            flags = data[i + 3]
            has_payload = bool(flags & 0x10)
            has_adaptation = bool(flags & 0x20)
            counter = flags & 0x0F
            if has_payload:
                last = self.continuity.get(pid)
                if last is not None:
                    gap = (counter - last - 1) & 0x0F
                    # A repeated counter is a duplicate packet, allowed by the standard.
                    if gap and counter != last:
                        self.lost += gap
                self.continuity[pid] = counter
            if has_payload and data[i + 1] & 0x40:  # a new PES packet starts here
                start = i + 4
                if has_adaptation:
                    start += 1 + data[i + 4]
                header = data[start:start + 4]
                if len(header) == 4 and header[:3] == b"\x00\x00\x01" \
                        and 0xE0 <= header[3] <= 0xEF:
                    self.video_pid = pid
                    self._frame(now)

    def _frame(self, now):
        self.frames += 1
        if self.first_frame_time is None:
            self.first_frame_time = now
            self.log.info("video stream found, PID %d", self.video_pid)
        if self.in_stall:
            duration = now - self.last_frame_time
            self.longest_stall = max(self.longest_stall, duration)
            self.stalled_time += duration
            self.in_stall = False
            self.log.warning("video resumed after %.0f ms", duration * 1000)
        self.last_frame_time = now

    def check_stall(self, now):
        if self.last_frame_time is None or self.in_stall:
            return
        if now - self.last_frame_time > self.stall_s:
            self.in_stall = True
            self.stalls += 1
            self.log.warning("video stalled: no frame for %.0f ms", self.stall_s * 1000)


class DecoderMessages(threading.Thread):
    """Copies what the decoder of ffplay complains about into the log."""

    def __init__(self, stream, log):
        super().__init__(daemon=True)
        self.stream = stream
        self.log = log
        self.count = 0

    def run(self):
        for raw in self.stream:
            line = raw.decode("utf-8", errors="replace").strip()
            if line and not any(text in line for text in HARMLESS):
                self.count += 1
                self.log.warning("decoder: %s", line)


def start_player(ffplay, video):
    url = "udp://127.0.0.1:%s?fifo_size=20000&overrun_nonfatal=1" % video["player_port"]
    command = [ffplay, "-hide_banner", "-loglevel", "warning", "-nostats",
               "-f", "mpegts", "-fflags", "nobuffer", "-flags", "low_delay",
               "-probesize", "32768", "-analyzeduration", "0",
               "-framedrop", "-sync", "ext",
               "-window_title", video["window_title"], url]
    return command, subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, help="UDP port; overrides udp_out_port")
    parser.add_argument("--no-player", action="store_true", help="counters only")
    parser.add_argument("--seconds", type=float, default=0, help="stop after this time")
    parser.add_argument("--key-file", help="override the embedded key with a 32-byte binary key file")
    args = parser.parse_args()
    decryptor = Decoder(load_key(args.key_file) if args.key_file else EMBEDDED_AES_KEY)

    main_cfg, video = video_common.read_config("rx.ini", VIDEO_DEFAULTS, MAIN_DEFAULTS)
    port = args.port or int(main_cfg["udp_out_port"])
    log = video_common.open_log("video_rx", video["log_file"])
    # The once-a-second line goes to the console only.
    console = log.handlers[0]

    listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 21)
    listener.bind(("127.0.0.1", port))
    listener.settimeout(0.05)
    forward = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    player_address = ("127.0.0.1", int(video["player_port"]))
    log.info("start: listening on UDP %d, player on UDP %d", port, player_address[1])

    player = decoder = None
    if not args.no_player:
        ffplay = video_common.find_tool("ffplay", video["ffmpeg_dir"])
        command, player = start_player(ffplay, video)
        log.info("command: %s", subprocess.list2cmdline(command))
        decoder = DecoderMessages(player.stderr, log)
        decoder.start()

    counters = TsCounters(int(video["stall_ms"]) / 1000.0, log)
    started = next_console = next_log = time.monotonic()
    second = dict(frames=0, bytes=0, lost=0)
    period = dict(frames=0, bytes=0, lost=0)
    lowest_fps = None
    try:
        while True:
            try:
                data = listener.recv(65536)
            except socket.timeout:
                data = b""
            now = time.monotonic()
            if data:
                data = decryptor.feed(data)
            if data:
                try:
                    forward.sendto(data, player_address)
                except OSError:
                    pass  # nobody listens on the player port
                counters.feed(data, now)
            counters.check_stall(now)

            if now >= next_console + CONSOLE_PERIOD:
                next_console += CONSOLE_PERIOD
                fps = counters.frames - second["frames"]
                kbit = (counters.payload_bytes - second["bytes"]) * 8 / 1000.0
                lost = counters.lost - second["lost"]
                second = dict(frames=counters.frames, bytes=counters.payload_bytes,
                              lost=counters.lost)
                if counters.first_frame_time is not None \
                        and now - counters.first_frame_time > 2.0:
                    lowest_fps = fps if lowest_fps is None else min(lowest_fps, fps)
                console.stream.write(
                    "%6.0f s  fps %3d  %5.0f kbit/s  lost %d (total %d)  stalls %d  "
                    "decode errors %d\n" % (
                        now - started, fps, kbit, lost, counters.lost, counters.stalls,
                        decoder.count if decoder else 0))
                console.stream.flush()
            if now >= next_log + LOG_PERIOD:
                next_log += LOG_PERIOD
                log.info("AES-GCM rejected records: %d", decryptor.rejected)
                log.info(
                    "fps %.1f, bitrate %.0f kbit/s, lost packets %d, total lost %d, "
                    "stalls %d, decode errors %d",
                    (counters.frames - period["frames"]) / LOG_PERIOD,
                    (counters.payload_bytes - period["bytes"]) * 8 / 1000.0 / LOG_PERIOD,
                    counters.lost - period["lost"], counters.lost, counters.stalls,
                    decoder.count if decoder else 0)
                period = dict(frames=counters.frames, bytes=counters.payload_bytes,
                              lost=counters.lost)

            if player is not None and player.poll() is not None:
                log.info("player window closed")
                break
            if args.seconds and now - started >= args.seconds:
                break
    except KeyboardInterrupt:
        pass
    finally:
        if player is not None and player.poll() is None:
            player.terminate()
            try:
                player.wait(timeout=3)
            except subprocess.TimeoutExpired:
                player.kill()
        listener.close()
        forward.close()

    now = time.monotonic()
    if counters.in_stall:
        duration = now - counters.last_frame_time
        counters.longest_stall = max(counters.longest_stall, duration)
        counters.stalled_time += duration
    video_time = (now - counters.first_frame_time) if counters.first_frame_time else 0.0
    packets = counters.payload_bytes // TS_LEN
    log.info("AES-GCM rejected records: %d", decryptor.rejected)
    log.info("stop. Totals:")
    log.info("  run time:          %.0f s, video for %.0f s", now - started, video_time)
    log.info("  frames:            %d, average %.1f fps, lowest second %s fps",
             counters.frames, counters.frames / video_time if video_time else 0.0,
             "-" if lowest_fps is None else lowest_fps)
    log.info("  lost TS packets:   %d (%.3f %%)", counters.lost,
             100.0 * counters.lost / (packets + counters.lost) if packets else 0.0)
    log.info("  stalls:            %d, longest %.0f ms, in total %.1f s",
             counters.stalls, counters.longest_stall * 1000, counters.stalled_time)
    log.info("  decode errors:     %d", decoder.count if decoder else 0)


if __name__ == "__main__":
    main()
