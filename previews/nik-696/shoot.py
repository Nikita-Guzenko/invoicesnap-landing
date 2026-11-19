#!/usr/bin/env python3
"""Full-page screenshots of the NIK-696 homepage previews (19 Nov 2026).

Usage: python3 previews/nik-696/shoot.py [option-1 option-2 ...]   (default: every option-* folder + current)
Serves the repo root over HTTP on a free port, so /assets/ and /previews/ paths resolve like on the site.
Writes previews/nik-696/shots/<name>-1440.png and -390.png. "current" = src/pages/index.html (the live page source).
Gotcha: animations start on load and on scroll; the script scrolls the page to the end and waits, so every
reveal-on-scroll block is visible in the full-page image.
"""
import glob, http.server, socketserver, sys, threading, time
from functools import partial
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
D = Path(__file__).resolve().parent
OUT = D / "shots"; OUT.mkdir(exist_ok=True)

class Handler(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path):
        p = path.split("?")[0]
        if p.startswith("/assets/"):  # site assets live in src/static/assets
            return str(ROOT / "src/static" / p.lstrip("/"))
        if p == "/current/":
            return str(ROOT / "src/pages/index.html")
        return super().translate_path(path)
    def log_message(self, *a): pass

names = sys.argv[1:] or ["current"] + sorted(Path(p).name for p in glob.glob(str(D / "option-*")))
srv = socketserver.TCPServer(("127.0.0.1", 0), partial(Handler, directory=str(ROOT)))
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
exe = glob.glob(str(Path.home() / ".cache/ms-playwright/chromium-1200/chrome-linux*/chrome"))[0]
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=exe)
    for n in names:
        url = f"http://127.0.0.1:{port}/current/" if n == "current" else f"http://127.0.0.1:{port}/previews/nik-696/{n}/"
        for w, h, mobile in ((1440, 900, False), (390, 844, True)):
            pg = b.new_page(viewport={"width": w, "height": h}, device_scale_factor=1 if w > 400 else 2,
                            is_mobile=mobile, has_touch=mobile)
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.on("requestfailed", lambda r: errs.append("failed " + r.url))
            pg.goto(url, wait_until="networkidle")
            total = pg.evaluate("document.documentElement.scrollHeight")
            for y in range(0, total + h, h // 2):
                pg.evaluate(f"window.scrollTo(0,{y})"); time.sleep(0.12)
            pg.evaluate("window.scrollTo(0,0)"); time.sleep(1.5)
            overflow = pg.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
            pg.screenshot(path=str(OUT / f"{n}-{w}.png"), full_page=True)
            print(f"{n} {w}px height={total} overflow={overflow} errors={errs[:3]}")
            pg.close()
    b.close()
srv.shutdown()
