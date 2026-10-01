"""Video side of the TRANSMITTER: camera -> H.264 -> MPEG-TS -> AES-256-GCM -> UDP -> fpv_tx.

Runs ffmpeg with low-latency settings and keeps a log (video_tx.log) with the
frame rate and bit rate. Settings come from the [video] section of tx.ini;
the UDP port is udp_in_port from [main], the same one fpv_tx.grc listens on.
The link capacity is worked out from the modem settings in common.ini.

Usage:
    python video_tx.py   run until Ctrl+C
    python video_tx.py --list           show the names of the cameras
    python video_tx.py --bitrate 400    override bitrate_kbps from tx.ini
    python video_tx.py --source test    test picture instead of the camera
"""
import argparse
import glob
import os
import re
import subprocess
import sys
import threading
import time

import video_common
from video_crypto import EncryptRelay, FrameEncoder, load_key

# Shared secret: anyone with this source file can extract the key.
EMBEDDED_AES_KEY = bytes.fromhex("075361aa0d6e6db640b40efd064a467c7921072d721d21ae4399216b9115c92c")

VIDEO_DEFAULTS = dict(
    source="camera", camera_name="", width="640", height="480", fps="25",
    bitrate_kbps="800", preset="veryfast", keyint="25", intra_refresh="1",
    log_file="video_tx.log", ffmpeg_dir="",
    capture_width="", capture_height="", capture_fps="")
MAIN_DEFAULTS = dict(samp_rate="2000000", sps="2", ts_per_frame="4",
                     fec_enabled="1", udp_in_port="5000")

TS_LEN = 188
# Messages of ffmpeg that are not problems (full-range colours of webcams).
HARMLESS = ("deprecated pixel format",)
FRAME_OVERHEAD = 8 + 3 + 4  # modem frame: sync and length + header + CRC32
TS_MARGIN = 1.10            # MPEG-TS headers on top of the video bit rate
VBV_SECONDS = 0.2           # encoder rate buffer; small = even bit rate, low delay
REPORT_PERIOD = 10.0
RESTART_DELAY = 1.0


def list_cameras(ffmpeg):
    if os.name != "nt":
        # Linux: USB cameras only; the Raspberry Pi 5 has video nodes of its
        # own (HEVC decoder, ISP) that are not cameras. Every camera has one
        # capture node with index 0; the other nodes carry only metadata.
        cameras = []
        for sys_dir in sorted(glob.glob("/sys/class/video4linux/video*"),
                              key=lambda d: int(re.sub(r"\D", "", os.path.basename(d)))):
            if "/usb" not in os.path.realpath(os.path.join(sys_dir, "device")):
                continue
            try:
                with open(os.path.join(sys_dir, "index")) as f:
                    if f.read().strip() != "0":
                        continue
            except OSError:
                pass
            cameras.append("/dev/" + os.path.basename(sys_dir))
        return cameras
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
        capture_output=True)
    text = result.stderr.decode("utf-8", errors="replace")
    return re.findall(r'"([^"]+)" \(video\)', text)


def link_capacity(main):
    """Net bit rate the modem offers for MPEG-TS, bit/s."""
    payload = TS_LEN * int(main["ts_per_frame"])
    rate = float(main["samp_rate"]) / int(main["sps"]) * 2
    if int(main["fec_enabled"]):
        rate *= 0.5
    return rate * payload / (payload + FRAME_OVERHEAD)


def input_args(video, ffmpeg, log):
    source = video["source"].strip()
    size = "%sx%s" % (video["width"], video["height"])
    if source == "camera":
        size = "%sx%s" % (video.get("capture_width") or video["width"],
                            video.get("capture_height") or video["height"])
        capture_fps = video.get("capture_fps") or video["fps"]
        name = video["camera_name"].strip()
        if not name:
            cameras = list_cameras(ffmpeg)
            if not cameras:
                return None
            name = cameras[0]
        if os.name != "nt":
            return ["-f", "v4l2", "-video_size", size, "-framerate", capture_fps, "-i", name]
        return ["-f", "dshow", "-rtbufsize", "8M", "-video_size", size,
                "-framerate", capture_fps, "-i", "video=" + name]
    if source == "test":
        # Diagnostic picture with a running frame counter and clock.
        return ["-re", "-f", "lavfi", "-i",
                "testsrc2=size=%s:rate=%s" % (size, video["fps"])]
    return ["-re", "-stream_loop", "-1", "-i", source]  # diagnostic file, looped


def build_command(ffmpeg, main, video, log):
    """ffmpeg command line, or None while no camera is connected."""
    source = input_args(video, ffmpeg, log)
    if source is None:
        return None
    bitrate = int(video["bitrate_kbps"])
    x264 = "keyint=%s:min-keyint=%s" % (video["keyint"], video["keyint"])
    if int(video["intra_refresh"]):
        # No large key frames: the picture is refreshed column by column,
        # so the bit rate stays even and nothing queues up before the modem.
        x264 += ":intra-refresh=1"
    else:
        # Repeat decoder configuration with regular independently decodable IDRs.
        x264 += ":intra-refresh=0:open-gop=0:scenecut=0:repeat-headers=1"
    return [ffmpeg, "-hide_banner", "-loglevel", "warning", "-nostats",
            "-progress", "pipe:1", "-stats_period", "1"] + source + [
        "-an",
        "-vf", "scale=%s:%s,fps=%s,format=yuv420p" % (
            video["width"], video["height"], video["fps"]),
        "-c:v", "libx264", "-preset", video["preset"], "-tune", "zerolatency",
        "-b:v", "%dk" % bitrate, "-maxrate", "%dk" % bitrate,
        "-bufsize", "%dk" % max(1, int(bitrate * VBV_SECONDS)),
        "-x264-params", x264,
        "-f", "mpegts", "-flush_packets", "1",
        "udp://127.0.0.1:%s?pkt_size=%d" % (main.get("crypto_input_port", main["udp_in_port"]), int(main.get("crypto_plain_packets", 7)) * TS_LEN)]


class Progress(threading.Thread):
    """Reads the key=value progress report of ffmpeg and logs a summary."""

    def __init__(self, stream, log):
        super().__init__(daemon=True)
        self.stream = stream
        self.log = log

    def run(self):
        values = {}
        last_report = time.monotonic()
        last_frame = last_size = 0
        for raw in self.stream:
            key, _, value = raw.decode("utf-8", errors="replace").strip().partition("=")
            values[key] = value
            if key != "progress":
                continue
            now = time.monotonic()
            if now - last_report < REPORT_PERIOD:
                continue
            try:
                frame = int(values.get("frame", 0))
                size = int(values.get("total_size", 0))
            except ValueError:
                continue
            period = now - last_report
            self.log.info(
                "fps %.1f, bitrate %.0f kbit/s, frames %d, dropped %s, duplicated %s",
                (frame - last_frame) / period, (size - last_size) * 8 / 1000.0 / period,
                frame, values.get("drop_frames", "?"), values.get("dup_frames", "?"))
            last_report, last_frame, last_size = now, frame, size


class Messages(threading.Thread):
    """Copies the warnings and errors of ffmpeg into the log."""

    def __init__(self, stream, log):
        super().__init__(daemon=True)
        self.stream = stream
        self.log = log

    def run(self):
        for raw in self.stream:
            line = raw.decode("utf-8", errors="replace").strip()
            if line and not any(text in line for text in HARMLESS):
                self.log.warning("ffmpeg: %s", line)


def stop_process(process):
    """Asks ffmpeg to finish ('q'), and ends it by force if it does not."""
    if process.poll() is None:
        try:
            process.stdin.write(b"q")
            process.stdin.flush()
        except OSError:
            pass
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true", help="show the cameras and exit")
    parser.add_argument("--source", help="camera, test, or a file; overrides tx.ini")
    parser.add_argument("--bitrate", type=int, help="kbit/s; overrides tx.ini")
    parser.add_argument("--port", type=int, help="UDP port; overrides udp_in_port")
    parser.add_argument("--seconds", type=float, default=0, help="stop after this time")
    parser.add_argument("--key-file", help="override the embedded key with a 32-byte binary key file")
    args = parser.parse_args()

    main_cfg, video = video_common.read_config("tx.ini", VIDEO_DEFAULTS, MAIN_DEFAULTS)
    ffmpeg = video_common.find_tool("ffmpeg", video["ffmpeg_dir"])
    if args.list:
        for name in list_cameras(ffmpeg):
            print(name)
        return
    key = load_key(args.key_file) if args.key_file else EMBEDDED_AES_KEY
    if args.source:
        video["source"] = args.source
    if args.bitrate:
        video["bitrate_kbps"] = str(args.bitrate)
    if args.port:
        main_cfg["udp_in_port"] = str(args.port)

    log = video_common.open_log("video_tx", video["log_file"])
    capacity = link_capacity(main_cfg) / 1000.0
    framing = FrameEncoder(key, int(main_cfg["ts_per_frame"]))
    overhead = framing.frame_packets / framing.plain_packets
    needed = int(video["bitrate_kbps"]) * TS_MARGIN * overhead
    log.info("start: %sx%s at %s fps, %s kbit/s; link capacity %.0f kbit/s",
             video["width"], video["height"], video["fps"], video["bitrate_kbps"], capacity)
    if needed > capacity:
        log.warning("video needs about %.0f kbit/s with MPEG-TS headers, the link offers "
                    "%.0f kbit/s: lower bitrate_kbps in tx.ini", needed, capacity)

    relay = EncryptRelay(key, int(main_cfg["udp_in_port"]), int(main_cfg["ts_per_frame"]))
    main_cfg["crypto_input_port"] = str(relay.port)
    main_cfg["crypto_plain_packets"] = str(framing.plain_packets)
    relay.start()
    log.info("AES-256-GCM: %d input TS per %d-packet modem frame; overhead %.0f%% "
             "for full records, up to 20 ms buffering for short records",
             framing.plain_packets, framing.frame_packets, (overhead - 1) * 100)
    started = time.monotonic()
    process = None
    last_command = None
    waiting = False
    try:
        while True:
            if relay.error is not None:
                raise RuntimeError("Encryption relay failed") from relay.error
            # The camera is looked up again before every start of ffmpeg, so a
            # camera plugged in later, or reconnected, is found by itself.
            command = build_command(ffmpeg, main_cfg, video, log)
            if command is None:
                if not waiting:
                    log.warning("no camera found, waiting for one (list: video_tx.py --list)")
                    waiting = True
                if args.seconds and time.monotonic() - started >= args.seconds:
                    raise KeyboardInterrupt
                time.sleep(RESTART_DELAY)
                continue
            waiting = False
            if command != last_command:
                log.info("command: %s", subprocess.list2cmdline(command))
                last_command = command
            process = subprocess.Popen(command, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            Progress(process.stdout, log).start()
            Messages(process.stderr, log).start()
            while process.poll() is None:
                if relay.error is not None:
                    raise RuntimeError("Encryption relay failed") from relay.error
                time.sleep(0.2)
                if args.seconds and time.monotonic() - started >= args.seconds:
                    raise KeyboardInterrupt
            log.error("ffmpeg stopped with code %s, restarting in %.0f s",
                      process.returncode, RESTART_DELAY)
            time.sleep(RESTART_DELAY)
    except KeyboardInterrupt:
        pass
    finally:
        if process is not None:
            stop_process(process)
        relay.close()
        log.info("stop")


if __name__ == "__main__":
    main()
