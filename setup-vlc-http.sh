#!/usr/bin/env bash
# Enable VLC's built-in web interface for every future VLC launch, and store
# the credentials where vlc_remote.py will find them.
set -euo pipefail

VLCRC="$HOME/.config/vlc/vlcrc"
CONF="$HOME/.config/vlc-remote.json"
VLC_PORT=8080
REMOTE_PORT=8099
FORCE=0
[[ "${1:-}" == "--force" ]] && FORCE=1

say()  { printf '  %s\n' "$*"; }
head1(){ printf '\n\033[1m%s\033[0m\n' "$*"; }

head1 "VLC phone remote - setup"

if [[ ! -f "$VLCRC" ]]; then
  say "No $VLCRC yet. Launch VLC once, close it, then re-run this."
  exit 1
fi

if pgrep -x vlc >/dev/null && [[ $FORCE -eq 0 ]]; then
  say "VLC is running right now."
  say "It can overwrite its own config on exit, which would undo this setup."
  say "Quit VLC, then run this script again (or pass --force to go ahead anyway)."
  exit 1
fi

# Reuse an existing password if one is already configured, else make one.
PW="$(sed -n 's/^http-password=\(..*\)/\1/p' "$VLCRC" | tail -1)"
if [[ -z "$PW" ]]; then
  PW="$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 16)"
fi

cp -f "$VLCRC" "$VLCRC.bak-$(date +%Y%m%d%H%M%S)"

set_opt() {   # set_opt <key> <value>
  local k="$1" v="$2"
  if grep -qE "^#?${k}=" "$VLCRC"; then
    # escape / and & for sed's replacement side
    local esc; esc=$(printf '%s' "$v" | sed -e 's/[\/&]/\\&/g')
    # match to end of line: "#http-port=8080" must become "http-port=8080",
    # not "http-port=80808080".
    sed -i -E "0,/^#?${k}=.*$/s//${k}=${esc}/" "$VLCRC"
  else
    printf '%s=%s\n' "$k" "$v" >> "$VLCRC"
  fi
}

set_opt extraintf    http
set_opt http-password "$PW"
set_opt http-port     "$VLC_PORT"
# Bind VLC's own interface to loopback only: the phone talks to our remote
# server instead, so VLC's password-protected port is never on the network.
set_opt http-host     127.0.0.1

umask 077
cat > "$CONF" <<JSON
{
  "vlc_host": "127.0.0.1",
  "vlc_port": $VLC_PORT,
  "vlc_password": "$PW",
  "remote_port": $REMOTE_PORT,
  "media_root": "$HOME"
}
JSON
chmod 600 "$CONF"

say "vlcrc updated (backup kept next to it)"
say "credentials written to $CONF"

head1 "Two things left to do"
say "1) Let your phone through the firewall (asks for your password)."
say "   iOS Personal Hotspot always uses 172.20.10.0/28, so this rule keeps"
say "   working and exposes nothing to any other network:"
printf '\n     sudo ufw allow from 172.20.10.0/28 to any port %s proto tcp comment "vlc remote"\n' "$REMOTE_PORT"
# If this machine is on some other LAN right now, offer that subnet too.
CIDR="$(ip -o -4 addr show scope global 2>/dev/null \
        | grep -vE ' (docker|br-|veth|virbr)' | awk '{print $4}' | head -1)"
if [[ -n "${CIDR:-}" && "$CIDR" != 172.20.10.* ]]; then
  NET="$(python3 -c "import ipaddress,sys;print(ipaddress.ip_network(sys.argv[1],strict=False))" "$CIDR" 2>/dev/null || true)"
  [[ -n "$NET" ]] && printf '\n   and for the network you are on right now (%s):\n\n     sudo ufw allow from %s to any port %s proto tcp comment "vlc remote"\n' \
     "$CIDR" "$NET" "$REMOTE_PORT"
fi
printf '\n'
say "2) Start VLC again - the web interface is now on by default."

head1 "Then run the remote"
printf '\n     python3 %s/vlc_remote.py\n\n' "$(cd "$(dirname "$0")" && pwd)"
