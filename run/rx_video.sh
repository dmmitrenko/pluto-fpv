#!/bin/sh
# Video side of the receiver. Start fpv_rx.grc first, then this script.
# Arguments are passed on, for example: run/rx_video.sh --no-player
cd "$(dirname "$0")/../src" || exit 1
PY="$HOME/radioconda/bin/python"
[ -x "$PY" ] || PY=python3
PATH="$HOME/radioconda/bin:$PATH" exec "$PY" video_rx.py "$@"
