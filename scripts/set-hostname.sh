#!/usr/bin/env bash
# Give this board its own address so cloned units don't clash:  sudo ./set-hostname.sh flightboard-sam
# The settings page is then at http://flightboard-sam.local
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo"; exit 1; }
NEW="${1:-}"
if ! [[ "$NEW" =~ ^[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?$ ]]; then
  echo "Use lowercase letters, numbers and hyphens only, e.g. flightboard-sam"; exit 1
fi
OLD="$(hostname)"
hostnamectl set-hostname "$NEW"
sed -i "s/\b$OLD\b/$NEW/g" /etc/hosts
systemctl restart avahi-daemon
echo "Hostname is now $NEW. Settings page: http://$NEW.local  (reboot to be sure everything picks it up)"
