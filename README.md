# VLC phone remote — instruction manual

Control VLC on this laptop from your iPhone, over the local network.
No extra hardware, no app to install.

---

## After a restart: nothing to do

The remote starts by itself at boot. All three pieces are permanent:

| piece | state | survives reboot |
|---|---|---|
| `vlc-remote` background service | enabled, with linger | yes — starts before you even log in |
| VLC's web interface | written into `~/.config/vlc/vlcrc` | yes |
| firewall rule for port 8099 | `ufw` rule, `ufw` enabled at boot | yes |

So the whole routine after a restart is:

1. Open VLC and start a video the way you always do.
2. On the iPhone, open the remote (Home Screen icon, or the URL below).

That's it. If it doesn't work, go to **Troubleshooting**.

---

## The address

**http://daniel-toba.local:8099**

If that ever fails to resolve, use the laptop's IP directly:

**http://172.20.10.2:8099**

When tethered to your iPhone's Personal Hotspot the laptop is nearly always
`172.20.10.2`, but to check the current one:

```bash
ip -4 -o addr show wlp2s0 | awk '{print $4}'
```

Two things that will never work from the phone:

- `localhost:8099` — on the phone that means *the phone*, not the laptop.
- `daniel-toba.local:8099` typed without `http://` — Safari turns it into a
  web search. Type the `http://` part.

### Add it to the Home Screen

In Safari: **Share → Add to Home Screen**. It then opens full-screen with its
own icon, like an app.

---

## What the remote can do

| tab | controls |
|---|---|
| **Remote** | play/pause, next/previous/stop, scrubber, ±10s and ±60s, volume, mute, speed, full screen, aspect ratio, one-tap subtitle toggle |
| **Tracks** | pick a subtitle track by name or turn it off, cycle tracks, subtitle delay ±50 ms, subtitle size and position, pick an audio track, audio delay |
| **Library** | browse your folders, tap a name to play it now, tap **＋** to add it to the queue. Tapping a `.srt` loads it as a subtitle for what's playing |
| **Queue** | see what's lined up, tap any item to jump to it, shuffle, loop |

---

## Troubleshooting

### The phone can't open the page at all

Check the remote is running:

```bash
systemctl --user status vlc-remote
```

If it isn't `active`, start it:

```bash
systemctl --user restart vlc-remote
```

Check it's actually listening:

```bash
ss -tlnp | grep 8099
```

You want to see `0.0.0.0:8099`. If it says `127.0.0.1:8099`, it's only
listening to itself and the phone can never reach it.

### The page loads but says "Can't reach VLC's web interface"

That means the remote is fine and VLC is the problem. Either VLC isn't
running — start it and play something — or VLC lost its web-interface
settings. Check them:

```bash
grep -E "^(extraintf|http-host|http-port|http-password)=" ~/.config/vlc/vlcrc
```

You should get exactly four lines: `extraintf=http`, `http-host=127.0.0.1`,
`http-port=8080`, and a `http-password=` with a long random value.

**If any are missing, VLC overwrote them.** This happens when you change
settings inside VLC's own Preferences window, because VLC rewrites the whole
config file when it closes. Fix it by quitting VLC and re-running setup:

```bash
~/vlc-remote/setup-vlc-http.sh
```

It keeps your existing password, so nothing else needs changing. Then start
VLC again.

### It works on the hotspot but not on home Wi-Fi

The firewall rule only allows `172.20.10.0/28`, which is the range iOS
Personal Hotspot hands out. On a different network you need a rule for that
network too. Find the subnet:

```bash
ip -4 -o addr show wlp2s0 | awk '{print $4}'
```

If that prints something like `192.168.1.34/24`, the rule is:

```bash
sudo ufw allow from 192.168.1.0/24 to any port 8099 proto tcp comment "vlc remote"
```

To see what rules exist now:

```bash
sudo ufw status numbered
```

### Did the phone's request even arrive?

The service runs quietly. To watch requests live, stop it and run the server
by hand with `-v`:

```bash
systemctl --user stop vlc-remote && python3 ~/vlc-remote/vlc_remote.py -v
```

Now tap things on the phone. Every request prints with the client IP, and
your phone appears as `172.20.10.1` on the hotspot. **No lines at all** while
you tap means the packets never arrived — that's the firewall or the network,
not the remote.

Press Ctrl-C when done and put the service back:

```bash
systemctl --user start vlc-remote
```

### Subtitle delay buttons seem to do nothing

Subtitle delay only moves when a subtitle track is actually switched on.
Turn subtitles on first in the **Tracks** tab, then adjust the delay.

### The tick next to a subtitle track looks wrong

VLC never reports which track is currently playing, so the tick marks the one
*this remote* last chose. It clears whenever a new file starts — the track may
still be on even with no tick. Use **Subtitles** on the Remote tab to toggle,
or **Cycle subtitle track**, and watch the TV for VLC's own on-screen message.

---

## Managing the service

```bash
systemctl --user status vlc-remote
```

```bash
systemctl --user restart vlc-remote
```

```bash
systemctl --user stop vlc-remote
```

To stop it starting at boot:

```bash
systemctl --user disable --now vlc-remote
```

To run it by hand instead, with live output (stop the service first, or it
will complain the port is taken):

```bash
python3 ~/vlc-remote/vlc_remote.py -v
```

---

## Where everything lives

| path | what it is |
|---|---|
| `~/vlc-remote/vlc_remote.py` | the server: serves the page, forwards commands to VLC |
| `~/vlc-remote/app.html` | the phone interface — safe to edit, just restart the service |
| `~/vlc-remote/setup-vlc-http.sh` | turns VLC's web interface on; re-runnable |
| `~/vlc-remote/install-service.sh` | installs the boot service; re-runnable |
| `~/.config/vlc-remote.json` | port and VLC password (mode 600) |
| `~/.config/systemd/user/vlc-remote.service` | the service definition |
| `~/.config/vlc/vlcrc` | VLC's own config, where the web interface is enabled |

### How the pieces connect

```
iPhone  ──http://…:8099──▶  vlc_remote.py  ──127.0.0.1:8080──▶  VLC
         (hotspot network)   (this laptop)   (loopback only)
```

VLC's own interface is bound to loopback, so it is never exposed to the
network — only port 8099 is, and only to the hotspot subnet.

---

## Rebuilding from scratch

If the config is ever beyond repair, quit VLC and run:

```bash
~/vlc-remote/setup-vlc-http.sh && ~/vlc-remote/install-service.sh
```

Then re-add the firewall rule if it's missing:

```bash
sudo ufw allow from 172.20.10.0/28 to any port 8099 proto tcp comment "vlc remote"
```

---

## Notes

- Anyone on the same network who finds port 8099 can control playback. On your
  personal hotspot that's only your own devices. The server forwards only a
  fixed list of commands, confines file browsing to your home folder, and
  cannot be used to make VLC quit or open files elsewhere.
- Playback happens entirely on the laptop. The phone only sends commands, so
  it uses no bandwidth and the battery barely notices.

---

## License

Released under the [MIT License](LICENSE).
