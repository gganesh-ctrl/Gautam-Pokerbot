#!/usr/bin/env python3
"""
The arena: a local web page for seating bots at a table and running battles.

    python3 arena/server.py              # opens http://localhost:8765
    python3 arena/server.py --lan        # friends on the same Wi-Fi can open it and upload bots

Standard library only. Battles run in a separate process (arena/worker.py), so a broken
bot can't take the page down, and Stop really stops it.

Uploaded bots are Python code that runs on this computer. Only accept files from people
you trust, and only use --lan on a network you trust.
"""
import argparse
import ast
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EXTERNAL = os.path.join(ROOT, "bots", "external")
HISTORY = os.path.join(HERE, "history.json")
sys.path.insert(0, ROOT)

from bots import DEFAULT_FIELD, REGISTRY  # noqa: E402

MAX_UPLOAD = 400 * 1024
NAME_OK = re.compile(r"^[A-Za-z0-9_\-]{1,48}\.py$")


# ------------------------------------------------------------------ bot catalogue
def _first_line(doc):
    if not doc:
        return ""
    for line in doc.strip().splitlines():
        line = line.strip()
        if line:
            return line[:160]
    return ""


def inspect_file(path):
    """Read a bot file without running it: find the class with act(), and its docstring."""
    try:
        with open(path, encoding="utf-8") as f:
            src = f.read()
        tree = ast.parse(src)
    except SyntaxError as e:
        return {"ok": False, "problem": "Syntax error on line %s: %s" % (e.lineno, e.msg)}
    except (OSError, UnicodeDecodeError) as e:
        return {"ok": False, "problem": "Couldn't read the file: %s" % e}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            methods = {n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
            if "act" in methods:
                desc = _first_line(ast.get_docstring(node)) or _first_line(ast.get_docstring(tree))
                return {"ok": True, "cls": node.name, "desc": desc}
    return {"ok": False, "problem": "No class with an act(self, state) method was found."}


def catalogue():
    bots = []
    for name, cls in REGISTRY.items():
        bots.append({"id": name, "name": name, "kind": "built-in", "desc": _first_line(cls.__doc__), "ok": True})
    if os.path.isdir(EXTERNAL):
        for fn in sorted(os.listdir(EXTERNAL)):
            if not fn.endswith(".py") or fn.startswith("_"):
                continue
            info = inspect_file(os.path.join(EXTERNAL, fn))
            bots.append({"id": "bots/external/" + fn, "name": fn[:-3], "kind": "uploaded",
                         "desc": info.get("desc", ""), "ok": info["ok"], "problem": info.get("problem", ""),
                         "modified": os.path.getmtime(os.path.join(EXTERNAL, fn))})
    return {"bots": bots, "default_field": ["exploit"] + DEFAULT_FIELD}


# ------------------------------------------------------------------ battles
class Battles:
    def __init__(self):
        self.lock = threading.Lock()
        self.proc = None
        self.state = {"status": "idle"}
        self.history = []
        try:
            with open(HISTORY) as f:
                self.history = json.load(f)
        except (OSError, ValueError):
            self.history = []

    def start(self, cfg):
        with self.lock:
            if self.proc is not None and self.proc.poll() is None:
                return False, "A battle is already running. Stop it or wait for it to finish."
            seats = [s for s in cfg.get("seats", []) if s]
            if len(seats) < 2:
                return False, "Seat at least two bots."
            known = {b["id"] for b in catalogue()["bots"]}
            missing = [s for s in seats if s not in known]
            if missing:
                return False, "These bots aren't in the rack any more: %s" % ", ".join(missing)
            job = {"seats": seats,
                   "hands": max(16, min(200000, int(cfg.get("hands", 2000)))),
                   "duplicate": bool(cfg.get("duplicate", True)),
                   "time_limit": float(cfg.get("time_limit", 0.001)),
                   "illegal": "clamp" if cfg.get("illegal") == "clamp" else "fold",
                   "seed": int(cfg.get("seed", 1))}
            self.state = {"status": "running", "config": job, "started": time.time(), "done": 0,
                          "hands": job["hands"], "standings": [], "series": []}
            # bot output to stderr goes to a log file (a pipe could fill up and stall the battle)
            self.errlog = open(os.path.join(HERE, "last_battle.log"), "w")
            self.proc = subprocess.Popen([sys.executable, os.path.join(HERE, "worker.py"), json.dumps(job)],
                                         stdout=subprocess.PIPE, stderr=self.errlog, cwd=ROOT,
                                         universal_newlines=True, bufsize=1)
            threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()
            return True, ""

    def _read(self, proc):
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            with self.lock:
                if proc is not self.proc:
                    return
                t = msg.get("type")
                if t == "progress":
                    self.state.update(done=msg["done"], standings=msg["standings"], series=msg["series"],
                                      elapsed=msg["elapsed"])
                elif t == "done":
                    self.state.update(status="done", done=msg["done"], standings=msg["standings"],
                                      series=msg["series"], featured=msg["featured"], elapsed=msg["elapsed"])
                    self._save()
                elif t == "error":
                    self.state.update(status="error", message=msg["message"], detail=msg.get("detail", ""))
        proc.wait()
        with self.lock:
            if proc is self.proc and self.state.get("status") == "running":
                try:
                    with open(os.path.join(HERE, "last_battle.log")) as f:
                        err = f.read()[-1500:]
                except OSError:
                    err = ""
                self.state.update(status="error", message="The battle process exited unexpectedly.", detail=err)

    def stop(self):
        with self.lock:
            if self.proc is not None and self.proc.poll() is None:
                self.proc.kill()
                self.state.update(status="stopped")
                return True
            return False

    def _save(self):
        s = self.state
        winner = s["standings"][0]["pid"] if s.get("standings") else ""
        entry = {"id": int(s["started"] * 1000), "finished": time.time(), "config": s["config"],
                 "winner": winner, "standings": s["standings"], "series": s["series"],
                 "featured": s.get("featured", [])[:4], "elapsed": s.get("elapsed", 0)}
        self.history.insert(0, entry)
        del self.history[20:]
        try:
            with open(HISTORY, "w") as f:
                json.dump(self.history, f)
        except OSError:
            pass


BATTLES = Battles()


# ------------------------------------------------------------------ HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "pokerlab-arena"

    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length", 0))
        if n > MAX_UPLOAD * 2:
            return None
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return None

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/bots":
            self._json(catalogue())
        elif path == "/api/battle":
            with BATTLES.lock:
                self._json(dict(BATTLES.state))
        elif path == "/api/history":
            self._json([{k: h[k] for k in ("id", "finished", "config", "winner", "elapsed")} for h in BATTLES.history])
        elif path.startswith("/api/history/"):
            hid = path.rsplit("/", 1)[-1]
            match = [h for h in BATTLES.history if str(h["id"]) == hid]
            self._json(match[0] if match else {"error": "That battle isn't in the history."}, 200 if match else 404)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        path = self.path.split("?")[0]
        data = self._body()
        if data is None:
            return self._json({"error": "The request was too large or not valid JSON."}, 400)
        if path == "/api/upload":
            name = os.path.basename(str(data.get("filename", "")))
            content = data.get("content", "")
            if not NAME_OK.match(name):
                return self._json({"error": "Use a .py file name with letters, numbers, - or _ only."}, 400)
            if len(content.encode()) > MAX_UPLOAD:
                return self._json({"error": "That file is over 400 KB."}, 400)
            os.makedirs(EXTERNAL, exist_ok=True)
            path_out = os.path.join(EXTERNAL, name)
            existed = os.path.exists(path_out)
            with open(path_out, "w", encoding="utf-8") as f:
                f.write(content)
            info = inspect_file(path_out)
            self._json({"id": "bots/external/" + name, "replaced": existed, **info})
        elif path == "/api/delete":
            bid = str(data.get("id", ""))
            name = os.path.basename(bid)
            if not bid.startswith("bots/external/") or not NAME_OK.match(name):
                return self._json({"error": "Only uploaded bots can be removed."}, 400)
            try:
                os.remove(os.path.join(EXTERNAL, name))
            except OSError:
                return self._json({"error": "That file was already gone."}, 404)
            self._json({"ok": True})
        elif path == "/api/battle":
            ok, msg = BATTLES.start(data)
            self._json({"ok": ok, "error": msg}, 200 if ok else 409)
        elif path == "/api/stop":
            self._json({"ok": BATTLES.stop()})
        else:
            self._json({"error": "not found"}, 404)


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "your-computer's-IP"
    finally:
        s.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--lan", action="store_true", help="let other computers on your network open the arena")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    host = "0.0.0.0" if a.lan else "127.0.0.1"
    httpd = ThreadingHTTPServer((host, a.port), Handler)
    url = "http://localhost:%d" % a.port
    print("Arena running at %s" % url)
    if a.lan:
        print("Friends on the same network can open http://%s:%d" % (lan_ip(), a.port))
        print("Uploaded bots run as code on this computer. Only share this with people you trust.")
    print("Press Ctrl+C to stop.")
    if not a.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        BATTLES.stop()
        print("\nArena stopped.")


if __name__ == "__main__":
    main()
