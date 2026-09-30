#!/bin/bash
# Run on the PC: copies src to the Raspberry Pi, installs pi/tx.ini as its
# tx.ini, installs the fpv-video and fpv-modem services, enables them at boot
# and restarts them. Needs ssh access by key and sudo without password on the Pi.
#   pi/deploy.sh [user@host]         default: fpv@fpv-tx.local
set -e
HOST=${1:-fpv@fpv-tx.local}
REPO=$(cd "$(dirname "$0")/.." && pwd)
SSH="ssh -o BatchMode=yes $HOST"

read -r USER_NAME HOME_DIR < <($SSH 'echo "$(id -un) $HOME"')
SRC="$HOME_DIR/pluto-fpv/src"
echo "deploy to $HOST: $SRC"

$SSH "sudo systemctl stop fpv-modem fpv-video 2>/dev/null || true"

# Sources only: no logs, no generated .py, the Pi keeps its own tx.ini from pi/.
tar -C "$REPO/src" -cf - --exclude='*.log' --exclude='__pycache__' --exclude='*.bat' \
    --exclude='tx.ini' --exclude='fpv_tx.py' --exclude='fpv_rx.py' --exclude='fpv_*_*.py' . \
    | $SSH "mkdir -p '$SRC' && tar -C '$SRC' -xf - && rm -f '$SRC'/fpv_tx_headless*.py"
$SSH "cat > '$SRC/tx.ini'" < "$REPO/pi/tx.ini"

for unit in fpv-video fpv-modem; do
    sed -e "s|@USER@|$USER_NAME|g" -e "s|@SRC@|$SRC|g" "$REPO/pi/$unit.service" \
        | $SSH "sudo tee /etc/systemd/system/$unit.service > /dev/null"
done
$SSH "sudo systemctl daemon-reload && sudo systemctl enable --quiet fpv-video fpv-modem \
      && sudo systemctl start fpv-video fpv-modem"
echo "services enabled and started; state: ssh $HOST systemctl status fpv-video fpv-modem"
