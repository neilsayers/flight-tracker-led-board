#!/usr/bin/env bash
# Build the MASTER image. Run once on a fresh Raspberry Pi OS Lite (Bookworm, 32- or 64-bit) on a Pi Zero 2 W, 3A+/3B+ or 4:
#   sudo ./scripts/install.sh
# Then reboot. Afterwards run scripts/prepare-clone.sh before you copy the SD card.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo"; exit 1; }

SRC="$(cd "$(dirname "$0")/.." && pwd)"
DEST=/opt/flightboard

apt-get update
# build-essential and rsync are not on a minimal Lite image; the LED library is compiled from source
apt-get install -y build-essential rsync python3-dev python3-venv python3-pil python3-flask cython3 cmake git avahi-daemon network-manager

# Copy the app. No --delete, so re-running this on a configured unit never removes its settings, counters or logos.
mkdir -p "$DEST"
rsync -a --exclude config.json --exclude state.json --exclude track.json --exclude out --exclude .venv \
  --exclude __pycache__ --exclude .git "$SRC"/ "$DEST"/
chmod 755 "$DEST"

# Airline logos are not in the repo (they are trademarks): download them if this copy has none
ls "$DEST"/logos/*.png >/dev/null 2>&1 || "$DEST"/scripts/fetch-logos.sh "$DEST"/logos

# venv that can see apt's Pillow and Flask, plus the matrix bindings built from source
python3 -m venv --system-site-packages "$DEST/.venv"
"$DEST/.venv/bin/pip" install git+https://github.com/hzeller/rpi-rgb-led-matrix

# The matrix library refuses to run with the onboard sound module loaded
CFG=/boot/firmware/config.txt; [ -f "$CFG" ] || CFG=/boot/config.txt
grep -q '^dtparam=audio=off' "$CFG" || sed -i 's/^dtparam=audio=on/dtparam=audio=off/' "$CFG"
grep -q '^dtparam=audio=off' "$CFG" || echo 'dtparam=audio=off' >> "$CFG"
echo 'blacklist snd_bcm2835' > /etc/modprobe.d/blacklist-rgb-matrix.conf
# (Pi OS often has no initramfs; the audio=off line above is what really matters)
command -v update-initramfs >/dev/null && update-initramfs -u || true

# Dedicate core 3 to the display refresh (reduces flicker)
CMD=/boot/firmware/cmdline.txt; [ -f "$CMD" ] || CMD=/boot/cmdline.txt
grep -q isolcpus "$CMD" || sed -i '1 s/$/ isolcpus=domain,managed_irq,3 nohz_full=3 rcu_nocbs=3 irqaffinity=0,1,2/' "$CMD"

install -m 644 "$DEST"/deploy/flightboard-display.service /etc/systemd/system/
install -m 644 "$DEST"/deploy/flightboard-web.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable flightboard-display flightboard-web avahi-daemon

echo "Done. Reboot now:  sudo reboot"
