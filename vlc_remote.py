#!/usr/bin/env python3
"""
vlc-remote - turn a phone into a VLC remote for this machine.

Serves a touch-friendly web remote and proxies it to VLC's built-in HTTP
interface, so the phone never has to deal with VLC's Basic-auth password.

  python3 vlc_remote.py                 # uses ~/.config/vlc-remote.json
  python3 vlc_remote.py --vlc-password pw --port 8099

VLC itself must be started with its web interface enabled; setup-vlc-http.sh
makes that permanent.
"""

import argparse
import base64
import json
import os
import socket
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.expanduser("~/.config/vlc-remote.json")
HOME = os.path.expanduser("~")

# Commands we are willing to forward to VLC. Anything else is rejected, so a
# curious device on the network cannot, say, tell VLC to quit.
ALLOWED_COMMANDS = {
    "pl_play", "pl_pause", "pl_forcepause", "pl_forceresume", "pl_stop",
    "pl_next", "pl_previous", "pl_delete", "pl_empty", "pl_loop", "pl_repeat",
    "pl_random", "seek", "volume", "fullscreen", "key", "rate",
    "subtitle_track", "audio_track", "video_track", "aspectratio",
    "in_play", "in_enqueue", "addsubtitle",
}

# Hotkey actions reachable through command=key (verified present in vlcrc).
ALLOWED_KEYS = {
    "play-pause", "subtitle-track", "subtitle-toggle", "subtitle-revtrack",
    "subdelay-up", "subdelay-down", "subpos-up", "subpos-down",
    "subtitle-text-scale-up", "subtitle-text-scale-down",
    "subtitle-text-scale-normal", "subsync-markaudio", "subsync-marksub",
    "subsync-apply", "subsync-reset", "audio-track", "audiodelay-up",
    "audiodelay-down", "rate-faster-fine", "rate-slower-fine", "rate-normal",
    "vol-up", "vol-down", "vol-mute", "aspect-ratio", "crop",
    "jump-extrashort", "jump-short", "jump-medium", "jump-long",
    "title-next", "title-prev", "chapter-next", "chapter-prev", "frame-next",
    "toggle-fullscreen", "leave-fullscreen", "next", "prev", "play",
}

MEDIA_EXT = {
    ".mkv", ".mp4", ".avi", ".mov", ".m4v", ".webm", ".wmv", ".flv", ".mpg",
    ".mpeg", ".ts", ".m2ts", ".ogv", ".3gp", ".divx", ".vob", ".rmvb",
    ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".wma",
    ".srt", ".sub", ".ass", ".ssa", ".vtt",
}
SUB_EXT = {".srt", ".sub", ".ass", ".ssa", ".vtt", ".idx"}


class VlcError(Exception):
    pass


class Vlc:
    """Thin client for VLC's lua HTTP interface."""

    def __init__(self, host, port, password, timeout=4.0):
        self.base = f"http://{host}:{port}"
        self.auth = base64.b64encode(f":{password}".encode()).decode()
        self.timeout = timeout
        # VLC never reports which track is currently selected, so remember what
        # we last asked for and forget it when the playing item changes.
        self._picked = {"subtitle": None, "audio": None}
        self._picked_for = None

    def get(self, path, params=None):
        url = self.base + path
        if params:
            # quote_via=quote, not the default quote_plus: VLC's lua reads "+"
            # literally, so a path with spaces would arrive as Modern+Family.
            url += "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
        req = urllib.request.Request(url, headers={"Authorization": "Basic " + self.auth})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                body = r.read()
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise VlcError("VLC rejected the password (check http-password in vlcrc)")
            raise VlcError(f"VLC returned HTTP {e.code}")
        except (urllib.error.URLError, socket.timeout, ConnectionError) as e:
            raise VlcError(f"Can't reach VLC's web interface ({e.__class__.__name__}). "
                           "Is VLC running with the web interface enabled?")
        try:
            return json.loads(body.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            raise VlcError("VLC sent a malformed reply")

    # -- high level -------------------------------------------------------
    def status(self, params=None):
        raw = self.get("/requests/status.json", params)
        return self._shape(raw)

    def command(self, command, val=None, extra=None):
        params = {"command": command}
        if val is not None:
            params["val"] = val
        if extra:
            params.update(extra)
        if command in ("subtitle_track", "audio_track") and val is not None:
            kind = "subtitle" if command.startswith("sub") else "audio"
            try:
                self._picked[kind] = int(val)
            except (TypeError, ValueError):
                pass
        if command == "key" and val in ("subtitle-track", "subtitle-toggle",
                                        "subtitle-revtrack", "audio-track"):
            # Cycled by VLC; we no longer know where it landed.
            self._picked["subtitle" if val.startswith("sub") else "audio"] = None
        return self.status(params)

    def _shape(self, raw):
        info = raw.get("information") or {}
        cats = info.get("category") or {}
        meta = cats.get("meta") or {}

        tracks = {"video": [], "audio": [], "subtitle": []}
        for name, cat in cats.items():
            if name == "meta" or not isinstance(cat, dict):
                continue
            if not name.lower().startswith("stream"):
                continue
            kind = str(cat.get("Type", "")).lower()
            if kind not in tracks:
                continue
            try:
                es_id = int(name.split()[-1])
            except ValueError:
                continue
            bits = []
            if cat.get("Language"):
                bits.append(str(cat["Language"]))
            if cat.get("Description"):
                bits.append(str(cat["Description"]))
            if not bits and cat.get("Codec"):
                bits.append(str(cat["Codec"]).split("(")[0].strip())
            label = " - ".join(bits) or f"Track {es_id}"
            tracks[kind].append({"id": es_id, "label": label,
                                 "codec": cat.get("Codec", "")})
        for v in tracks.values():
            v.sort(key=lambda t: t["id"])

        plid = raw.get("currentplid")
        if plid != self._picked_for:
            self._picked_for = plid
            self._picked = {"subtitle": None, "audio": None}

        title = (meta.get("title") or "").strip()
        filename = (meta.get("filename") or "").strip()
        # File names are usually more informative than the muxer's title tag.
        display = filename or title or "Nothing playing"
        if filename:
            pretty = os.path.splitext(filename)[0]
            pretty = pretty.replace(".", " ").replace("_", " ")
            display = " ".join(pretty.split())

        return {
            "ok": True,
            "state": raw.get("state", "stopped"),
            "time": raw.get("time", 0),
            "length": raw.get("length", 0),
            "position": raw.get("position", 0.0),
            "volume": raw.get("volume", 0),
            "rate": raw.get("rate", 1.0),
            "fullscreen": bool(raw.get("fullscreen")),
            "random": bool(raw.get("random")),
            "loop": bool(raw.get("loop")),
            "repeat": bool(raw.get("repeat")),
            "aspectratio": raw.get("aspectratio", "default"),
            "subtitledelay": raw.get("subtitledelay", 0),
            "audiodelay": raw.get("audiodelay", 0),
            "currentplid": plid,
            "title": display,
            "rawtitle": title,
            "filename": filename,
            "tracks": tracks,
            "picked": dict(self._picked),
        }

    def playlist(self):
        raw = self.get("/requests/playlist.json")
        items = []

        def walk(node):
            for child in node.get("children") or []:
                if child.get("type") == "node":
                    walk(child)
                elif child.get("type") == "leaf":
                    uri = child.get("uri", "")
                    name = child.get("name") or ""
                    base = os.path.basename(urllib.parse.unquote(urllib.parse.urlparse(uri).path))
                    items.append({
                        "id": child.get("id"),
                        "name": os.path.splitext(base)[0] or name,
                        "duration": child.get("duration", -1),
                        "current": child.get("current") == "current",
                    })

        walk(raw)
        return {"ok": True, "items": items}

    def browse(self, path):
        raw = self.get("/requests/browse.json", {"uri": "file://" + path})
        dirs, files = [], []
        for el in raw.get("element") or []:
            name = el.get("name", "")
            p = el.get("path", "")
            if name == "." or (name.startswith(".") and name != ".."):
                continue
            if el.get("type") == "dir":
                if name == "..":
                    continue
                dirs.append({"name": name, "path": p, "dir": True})
            else:
                ext = os.path.splitext(name)[1].lower()
                if ext in MEDIA_EXT:
                    files.append({"name": name, "path": p, "dir": False,
                                  "size": el.get("size", 0),
                                  "sub": ext in SUB_EXT})
        dirs.sort(key=lambda d: d["name"].lower())
        files.sort(key=lambda f: f["name"].lower())
        parent = os.path.dirname(path.rstrip("/")) or "/"
        return {"ok": True, "path": path, "parent": parent,
                "entries": dirs + files}


# ---------------------------------------------------------------------------
# app icon (drawn here so the home-screen shortcut has a real icon)
# ---------------------------------------------------------------------------
def make_icon(size=180):
    bg = (28, 30, 38)
    cone = (255, 122, 24)
    px = bytearray()
    for y in range(size):
        px.append(0)  # PNG filter: none
        for x in range(size):
            u, v = x / size, y / size
            c = bg
            # a traffic cone: triangle body + base bar
            if 0.78 <= v <= 0.87 and 0.17 <= u <= 0.83:
                c = cone
            elif 0.17 <= v < 0.78:
                half = 0.055 + (v - 0.17) * 0.42
                if abs(u - 0.5) <= half:
                    c = cone if not (0.40 <= v <= 0.52 and abs(u - 0.5) <= half - 0.03) else (255, 255, 255)
            px.extend(c)

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) +
            chunk(b"IDAT", zlib.compress(bytes(px), 9)) + chunk(b"IEND", b""))


MANIFEST = {
    "name": "VLC Remote",
    "short_name": "VLC",
    "display": "standalone",
    "background_color": "#14161c",
    "theme_color": "#14161c",
    "start_url": "/",
    "icons": [{"src": "/icon.png", "sizes": "180x180", "type": "image/png"}],
}


class Handler(BaseHTTPRequestHandler):
    server_version = "vlc-remote"
    protocol_version = "HTTP/1.1"
    vlc = None
    media_root = HOME
    _icon = None

    def log_message(self, fmt, *args):
        if self.server.verbose:
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # -- helpers ----------------------------------------------------------
    def _send(self, body, ctype="application/json", code=200, cache=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache or "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _safe_path(self, path):
        """Confine browsing to the media root so the remote can't walk the FS."""
        real = os.path.realpath(path)
        root = os.path.realpath(self.media_root)
        if real == root or real.startswith(root + os.sep):
            return real
        return root

    # -- routing ----------------------------------------------------------
    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(url.query)
        route = url.path.rstrip("/") or "/"
        try:
            if route == "/":
                with open(os.path.join(HERE, "app.html"), "rb") as f:
                    return self._send(f.read(), "text/html; charset=utf-8")
            if route == "/manifest.webmanifest":
                return self._send(MANIFEST, "application/manifest+json")
            if route == "/icon.png":
                if Handler._icon is None:
                    Handler._icon = make_icon()
                return self._send(Handler._icon, "image/png",
                                  cache="public, max-age=86400")
            if route == "/api/status":
                return self._send(self.vlc.status())
            if route == "/api/cmd":
                return self._send(self._do_cmd(q))
            if route == "/api/playlist":
                return self._send(self.vlc.playlist())
            if route == "/api/browse":
                start = q.get("path", [os.path.join(self.media_root, "Videos")])[0]
                if not os.path.isdir(start):
                    start = self.media_root
                return self._send(self.vlc.browse(self._safe_path(start)))
            if route == "/api/roots":
                return self._send({"ok": True, "root": self.media_root})
            return self._send({"ok": False, "error": "not found"}, code=404)
        except VlcError as e:
            return self._send({"ok": False, "error": str(e)})
        except BrokenPipeError:
            return
        except Exception as e:  # keep the remote alive whatever happens
            return self._send({"ok": False, "error": f"{e.__class__.__name__}: {e}"})

    def _do_cmd(self, q):
        command = (q.get("command") or [""])[0]
        val = (q.get("val") or [None])[0]
        if command not in ALLOWED_COMMANDS:
            return {"ok": False, "error": f"command '{command}' not allowed"}
        if command == "key" and val not in ALLOWED_KEYS:
            return {"ok": False, "error": f"key '{val}' not allowed"}
        extra = {}
        if command in ("in_play", "in_enqueue", "addsubtitle"):
            # val is a plain path from the Library tab; confine and encode it.
            if not val:
                return {"ok": False, "error": "missing path"}
            safe = self._safe_path(val)
            if not os.path.exists(safe):
                return {"ok": False, "error": "no such file"}
            key = "val" if command == "addsubtitle" else "input"
            val = None
            extra[key] = "file://" + urllib.parse.quote(safe)
            if command in ("in_play", "in_enqueue"):
                return self.vlc.command(command, None, extra)
        if command == "pl_play" and val is not None:
            return self.vlc.command(command, None, {"id": val})
        if command == "pl_delete" and val is not None:
            return self.vlc.command(command, None, {"id": val})
        return self.vlc.command(command, val, extra)


def local_ips():
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    for fam, _, _, _, addr in socket.getaddrinfo(socket.gethostname(), None):
        if fam == socket.AF_INET and addr[0] not in ips and not addr[0].startswith("127."):
            ips.append(addr[0])
    return ips


def load_config():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def main():
    cfg = load_config()
    ap = argparse.ArgumentParser(description="Phone remote for VLC")
    ap.add_argument("--port", type=int, default=cfg.get("remote_port", 8099),
                    help="port this remote listens on (default 8099)")
    ap.add_argument("--vlc-host", default=cfg.get("vlc_host", "127.0.0.1"))
    ap.add_argument("--vlc-port", type=int, default=cfg.get("vlc_port", 8080))
    ap.add_argument("--vlc-password",
                    default=os.environ.get("VLC_HTTP_PASSWORD", cfg.get("vlc_password", "")))
    ap.add_argument("--media-root", default=os.path.expanduser(cfg.get("media_root", HOME)),
                    help="folder the Library tab may browse (default: home)")
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    if not args.vlc_password:
        sys.exit("No VLC web-interface password configured.\n"
                 f"Run ./setup-vlc-http.sh first, or pass --vlc-password.")

    Handler.vlc = Vlc(args.vlc_host, args.vlc_port, args.vlc_password)
    Handler.media_root = os.path.realpath(args.media_root)

    httpd = ThreadingHTTPServer((args.bind, args.port), Handler)
    httpd.daemon_threads = True
    httpd.verbose = args.verbose

    host = socket.gethostname()
    print("VLC phone remote is up. On your iPhone, open:\n")
    print(f"    http://{host}.local:{args.port}")
    for ip in local_ips():
        print(f"    http://{ip}:{args.port}")
    print(f"\nLibrary browsing is limited to: {Handler.media_root}")
    print("Then tap Share -> Add to Home Screen for an app-like icon.")
    print("Ctrl-C to stop.\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
