#!/bin/sh
# Video side of the transmitter. Start fpv_tx.grc first, then this script.
# Arguments are passed on, for example: ./video_tx.sh --list
cd "$(dirname "$0")" || exit 1
PY="$HOME/radioconda/bin/python"
[ -x "$PY" ] || PY=python3
PATH="$HOME/radioconda/bin:$PATH" exec "$PY" video_tx.py "$@"
