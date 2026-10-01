"""On-screen display of the link state for the ffplay windows.

The overlay is drawn by ffplay itself: drawtext filters re-read small text files
every frame (reload=1), so nothing is re-encoded and no delay is added.

Three layers are stacked so the state can be colour-coded, which a single
drawtext cannot do (its colour is fixed when the filter is built):

    drawbox     one dark plate behind everything, for contrast
    headline    the same position twice, green and red; only one file has text
    details     the figures, in white

The radio figures come from the status line the modem writes to its log
(rx.log on the receiver, tx.log on the transmitter), the video figures from the
counters of the script that owns the window.
"""
import os
import re
import time

import video_common

MODEM_LOG = "rx.log"
TX_MODEM_LOG = "tx.log"
# A status line is written once per log_period_s; treat it as gone after this.
STALE_AFTER_S = 4.0
TAIL_BYTES = 8192
FIELD = re.compile(r"(\w+)=(-?[\d.]+)")
STAMP = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),(\d+)")

HEAD_SIZE = 20
TEXT_SIZE = 16
LINE_SPACING = 5
MARGIN = 14          # from the left and top edge of the picture
PADDING = 12         # between the plate edge and the text
# DejaVu Sans Mono is 0.602 em wide per character.
CHAR_WIDTH = 0.602
GOOD = "#49d17a"
BAD = "#ff5c5c"
FONTS = ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
         "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
         "C:\\Windows\\Fonts\\consola.ttf")


def _font_file():
    for path in FONTS:
        if os.path.isfile(path):
            return path
    return ""


class ModemStatus:
    """Last 'status:' line of the modem log, as a dict of numbers."""

    def __init__(self, log_file=MODEM_LOG):
        self.log_file = log_file
        self.fields = {}
        self.age = None

    def read(self):
        """Refreshes self.fields; self.age is None when the modem is not writing."""
        try:
            path = video_common.log_path(self.log_file)
            with open(path, "rb") as stream:
                stream.seek(0, os.SEEK_END)
                stream.seek(max(0, stream.tell() - TAIL_BYTES))
                tail = stream.read().decode("utf-8", errors="replace")
        except OSError:
            self.age = None
            return self.fields
        line = ""
        for candidate in tail.splitlines():
            if "INFO status:" in candidate:
                line = candidate
        stamp = STAMP.match(line) if line else None
        if not stamp:
            self.age = None
            return self.fields
        self.fields = {key: float(value) for key, value in FIELD.findall(line)}
        written = time.mktime(time.strptime(stamp.group(1), "%Y-%m-%d %H:%M:%S")) \
            + int(stamp.group(2)) / 1000.0
        self.age = max(0.0, time.time() - written)
        return self.fields

    def alive(self):
        return self.age is not None and self.age <= STALE_AFTER_S


class Osd:
    """Writes the overlay files that the drawtext filters of ffplay reload."""

    headline_ok = "LINK UP"
    headline_bad = "NO LINK"
    modem_log = MODEM_LOG

    def __init__(self, prefix="osd"):
        self.head_ok = video_common.log_path(prefix + "_ok.txt")
        self.head_bad = video_common.log_path(prefix + "_bad.txt")
        self.details = video_common.log_path(prefix + ".txt")
        self.status = ModemStatus(self.modem_log)
        self.columns = 34
        self.rows = 4
        self.write(False, "STARTING", ["waiting for the modem"])

    @staticmethod
    def _replace(path, text):
        # Replace in one step: drawtext must never read a half-written file.
        temporary = path + ".tmp"
        with open(temporary, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        os.replace(temporary, path)

    def write(self, good, headline, lines):
        self._replace(self.head_ok, headline + "\n" if good else "")
        self._replace(self.head_bad, "" if good else headline + "\n")
        self._replace(self.details, "\n".join(lines) + "\n")
        self.columns = max(len(headline), max(len(line) for line in lines))
        self.rows = len(lines)

    def _plate(self):
        """Geometry of the dark plate behind the text."""
        width = int(self.columns * TEXT_SIZE * CHAR_WIDTH) + 2 * PADDING
        height = HEAD_SIZE + LINE_SPACING + \
            self.rows * (TEXT_SIZE + LINE_SPACING) + 2 * PADDING
        return width, height

    def filter_argument(self):
        """The -vf value for ffplay, or '' when no usable font was found."""
        font = _font_file()
        if not font:
            return ""

        def escape(text):
            # ':' and '\' separate filter options, so they are escaped in paths.
            return text.replace("\\", "/").replace(":", "\\:")

        width, height = self._plate()
        text_x = MARGIN + PADDING
        head_y = MARGIN + PADDING
        body_y = head_y + HEAD_SIZE + LINE_SPACING

        def text(path, size, colour, y):
            # expansion=none: a '%' in the figures is printed, not taken for the
            # start of a drawtext expansion. borderw outlines every glyph, so the
            # text stays readable even where the plate is over a bright picture.
            return ("drawtext=fontfile='%s':textfile='%s':reload=1:expansion=none"
                    ":fontsize=%d:fontcolor=%s:line_spacing=%d"
                    ":borderw=2:bordercolor=black@0.85:x=%d:y=%d"
                    % (escape(font), escape(path), size, colour, LINE_SPACING,
                       text_x, y))

        return ",".join([
            "drawbox=x=%d:y=%d:w=%d:h=%d:color=black@0.55:t=fill"
            % (MARGIN, MARGIN, width, height),
            text(self.head_ok, HEAD_SIZE, GOOD, head_y),
            text(self.head_bad, HEAD_SIZE, BAD, head_y),
            text(self.details, TEXT_SIZE, "white", body_y),
        ])


class RxOsd(Osd):
    """Overlay for the receiver window: the state of the link that was decoded."""

    def update(self, fps, kbit, lost, total_lost, stalls, decode_errors, rejected):
        self.status.read()
        if not self.status.alive():
            self.write(False, "NO MODEM", ["fpv_rx is not writing " + self.modem_log])
            return
        f = self.status.fields
        locked = f.get("sync", 0) >= 1
        self.write(locked,
                   "%s   MER %.1f dB" % ("LINK UP" if locked else "NO SYNC",
                                         f.get("mer_db", 0.0)),
                   ["RF    %6.1f dBFS   %+6.1f kHz" % (
                       f.get("level_dbfs", 0.0), f.get("freq_offset_hz", 0.0) / 1000.0),
                    "RADIO %5.0f kb/s  crc %-4.0f lost %.0f" % (
                        f.get("kbit", 0.0), f.get("crc_errors", 0.0), f.get("lost", 0.0)),
                    "VIDEO %5.0f kb/s  %2.0f fps  lost %d" % (kbit, fps, total_lost),
                    "AES   GCM-256 %s  stalls %d  err %d" % (
                        "OK" if rejected == 0 else "REJ %d" % rejected,
                        stalls, decode_errors)])


class TxOsd(Osd):
    """Overlay for the transmitter preview: the picture as sent, before the radio."""

    modem_log = TX_MODEM_LOG

    def __init__(self, prefix="osd_tx"):
        super().__init__(prefix)

    def update(self, size, fps, kbit, frames, dropped, duplicated, overhead_percent):
        self.status.read()
        running = self.status.alive()
        f = self.status.fields
        self.write(running,
                   "%s   %s %.0f fps" % ("ON AIR" if running else "NO MODEM", size, fps),
                   ["H.264 %5.0f kb/s  frames %d" % (kbit, frames),
                    "MODEM %5.0f kb/s  data %-4.0f idle %.0f" % (
                        f.get("input_kbit", 0.0), f.get("data_frames", 0.0),
                        f.get("idle_frames", 0.0)),
                    "QUEUE %5.0f B  drop %-5.0f wait %.0f ms" % (
                        f.get("queue_bytes", 0.0), f.get("dropped_bytes", 0.0),
                        f.get("waiting_ms", 0.0)),
                    "CRYPT GCM-256 +%.0f%%  att %.0f dB  dd %s/%s" % (
                        overhead_percent, f.get("tx_attenuation", 0.0),
                        dropped, duplicated)])
