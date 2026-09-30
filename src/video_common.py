"""Shared helpers of video_tx.py and video_rx.py: config, log, finding ffmpeg."""
import configparser
import glob
import logging
import os
import shutil
import sys

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
COMMON_INI = "common.ini"

INSTALL_HINT = "install it with:  winget install Gyan.FFmpeg"


def read_config(ini_name, video_defaults, main_defaults):
    """Returns the [main] and [video] sections of the .ini as two dicts.

    [main] also holds the settings shared by both sides, from common.ini.
    """
    parser = configparser.ConfigParser()
    # common.ini is read last, so that it wins: the schematics take the
    # shared settings from it only.
    for name in (ini_name, COMMON_INI):
        path = os.path.join(SRC_DIR, name)
        if not parser.read(path, encoding="utf-8"):
            sys.exit("ERROR: config file not found: " + path)
    main = dict(main_defaults)
    video = dict(video_defaults)
    if parser.has_section("main"):
        main.update(parser["main"])
    if parser.has_section("video"):
        video.update(parser["video"])
    return main, video


def open_log(name, file_name):
    """Log to the file (next to the scripts) and to the console."""
    log = logging.getLogger(name)
    log.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    log.addHandler(console)
    if file_name:
        handler = logging.FileHandler(os.path.join(SRC_DIR, file_name), encoding="utf-8")
        handler.setFormatter(formatter)
        log.addHandler(handler)
    return log


def _user_path_dirs():
    # winget changes the PATH in the registry; a console opened before the
    # installation does not see it yet.
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value = winreg.QueryValueEx(key, "Path")[0]
        return [os.path.expandvars(d) for d in value.split(";") if d]
    except (ImportError, OSError):
        return []


def find_tool(name, ffmpeg_dir=""):
    """Full path of ffmpeg / ffplay, or exits with a hint how to install it."""
    exe = name + (".exe" if os.name == "nt" else "")
    candidates = []
    if ffmpeg_dir:
        candidates.append(os.path.join(ffmpeg_dir, exe))
    found = shutil.which(name)
    if found:
        candidates.append(found)
    candidates += [os.path.join(d, exe) for d in _user_path_dirs()]
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        candidates.append(os.path.join(local, "Microsoft", "WinGet", "Links", exe))
        candidates += glob.glob(os.path.join(
            local, "Microsoft", "WinGet", "Packages", "Gyan.FFmpeg*", "*", "bin", exe))
    for path in candidates:
        if os.path.isfile(path):
            return path
    sys.exit("ERROR: %s not found; %s" % (name, INSTALL_HINT))
