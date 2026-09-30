#!/bin/bash
# Transmitter modem without a window (Raspberry Pi), restarted whenever it stops.
# The modem exits when the Pluto disappears (unplugged, or restarted after a
# power dip); this script waits until the Pluto answers again and starts it anew.
# Also rebuilds fpv_tx_headless.py when fpv_tx_headless.grc is newer.
#   ./fpv_tx_run.sh          run until Ctrl+C
cd "$(dirname "$0")" || exit 1
PATH="$HOME/radioconda/bin:$PATH"
PY=python3
[ -x "$HOME/radioconda/bin/python" ] && PY="$HOME/radioconda/bin/python"
URI=$(sed -nE 's/^pluto_uri *= *(.+)$/\1/p' tx.ini | tr -d '\r')
RESTART_DELAY=2

log() {
    local line
    line="$(date '+%Y-%m-%d %H:%M:%S,%3N') $1 run: $2"
    echo "$line"
    echo "$line" >> tx.log
}

child=
stop() {
    [ -n "$child" ] && kill -INT "$child" 2>/dev/null && wait "$child"
    log INFO "stopped"
    exit 0
}
trap stop INT TERM

if [ ! -f fpv_tx_headless.py ] || [ fpv_tx_headless.grc -nt fpv_tx_headless.py ]; then
    log INFO "building fpv_tx_headless.py"
    grcc -o . fpv_tx_headless.grc > /dev/null || { log ERROR "grcc failed"; exit 1; }
fi

while true; do
    if ! iio_attr -u "$URI" -C hw_model > /dev/null 2>&1; then
        log WARNING "Pluto $URI does not answer, waiting"
        until iio_attr -u "$URI" -C hw_model > /dev/null 2>&1; do sleep 1; done
        log INFO "Pluto $URI is back"
    fi
    "$PY" -u fpv_tx_headless.py &
    child=$!
    wait "$child"
    code=$?
    child=
    log WARNING "modem stopped with code $code, restarting in $RESTART_DELAY s"
    sleep $RESTART_DELAY
done
