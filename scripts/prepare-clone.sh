#!/usr/bin/env bash
# Run on the master Pi as the LAST step before you shut it down and copy the SD card.
# Strips anything personal so every clone starts clean:  sudo ./scripts/prepare-clone.sh
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo"; exit 1; }

rm -f /opt/flightboard/config.json /opt/flightboard/state.json /opt/flightboard/track.json  # your test location, radius, spotted count
rm -f /etc/NetworkManager/system-connections/*          # saved Wi-Fi networks and passwords
rm -f /etc/ssh/ssh_host_*                               # Pi OS regenerates these on first boot
rm -f /root/.bash_history /home/*/.bash_history
journalctl --rotate && journalctl --vacuum-time=1s || true
hostnamectl set-hostname flightboard
sed -i "s/127\.0\.1\.1.*/127.0.1.1\tflightboard/" /etc/hosts

echo "Clean. Shutting down: copy the SD card once the green light stops flashing."
shutdown -h now
