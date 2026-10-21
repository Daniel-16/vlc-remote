#!/usr/bin/env bash
# Optional: keep the remote running in the background and start it at login.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
UNIT="$HOME/.config/systemd/user/vlc-remote.service"
mkdir -p "$(dirname "$UNIT")"

cat > "$UNIT" <<UNITEOF
[Unit]
Description=VLC phone remote
After=network-online.target

[Service]
ExecStart=/usr/bin/python3 $DIR/vlc_remote.py
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
UNITEOF

systemctl --user daemon-reload
systemctl --user enable --now vlc-remote.service
# Survive logout / run without an active session.
loginctl enable-linger "$USER" >/dev/null 2>&1 || true

printf '\nvlc-remote is running and will start automatically at login.\n\n'
printf '  status:  systemctl --user status vlc-remote\n'
printf '  logs:    journalctl --user -u vlc-remote -f\n'
printf '  stop:    systemctl --user stop vlc-remote\n'
printf '  disable: systemctl --user disable --now vlc-remote\n\n'
