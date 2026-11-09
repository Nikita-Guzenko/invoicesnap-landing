#!/usr/bin/env python3
"""Crawl the site over HTTP and check every search rule on every URL (NIK-545, 8 Nov 2026). Standard library only.

Usage:
  python3 check_site.py --dir dist                     serve dist/ on a local port and crawl it (before deploy)
  python3 check_site.py --base https://invoicesnap.work    crawl the live site (publish.py runs it after deploy)
Exit 0 = every check passed. Exit 1 = each problem is printed with its URL. tests/test_site.py proves that each check can fail.

It starts at /sitemap.xml and checks (playbook rules in brackets):
- the index lists only language sitemaps, and each one lists pages of that language [R03]
- each URL: HTTP 200 with no redirect, no noindex, one canonical in <head> that equals the URL, a title and a
  description unique in its language, <h1> and at least MIN_TEXT characters inside <main>, not hidden [R01].
  "Hidden" means the hidden attribute, <noscript> or <template>. Text hidden by CSS is not detected.
- hreflang: self link, x-default, valid tags, every target in a sitemap, and the same set on every page of a cluster
- JSON-LD parses, exists on each page, and WebPage, FAQPage values appear on the page [R12]
- internal <a> links return 200, have no query string, and every linked page is in a sitemap [R03, R31]
- robots.txt allows the six search robots and names the sitemap; the IndexNow key file answers [R36, R03]
- go-live gate on the free tool pages (data/tool-pages), on the bytes this crawl fetched: unique h1, gates 4 to 9 of
  gate.py, gates 2 and 3 of gate_browser.py in a real browser (--no-browser skips those two, for fixtures only).
  A page that the build blocked must answer with noindex and stay out of the sitemap. A failed page exits 1: publish stops.
"""
from __future__ import annotations

import argparse
import datetime
import functools
import http.client
import http.server
import json
import re
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import gate  # noqa: E402
import gate_browser  # noqa: E402
import tool_pages  # noqa: E402

ROBOTS = ("Googlebot", "Bingbot", "YandexBot", "OAI-SearchBot", "PerplexityBot", "Claude-SearchBot")  # R36 list
APP_REPO = Path.home() / "firstmate-invoicesnap" / "projects" / "invoicesnap-expo"  # Read only: gate 9 reads claim lines there.
MIN_TEXT = 200  # Below this a page is an empty shell: it shows that the text is not rendered by the server.
HREFLANG = re.compile(r"^(x-default|[a-z]{2,3}(-[A-Z][a-z]{3})?(-[A-Z]{2})?)$")
NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # A 301 is a finding here, so it must not be followed.


OPENER = urllib.request.build_opener(NoRedirect)


class Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool]] = []  # (tag, hidden)
        self.lang = ""
        self.title = ""
        self.description = ""
        self.canonicals: list[str] = []
        self.body_canonicals = 0
        self.robots: list[str] = []
        self.alternates: list[tuple[str, str]] = []
        self.ld: list[str] = []
        self.links: list[str] = []
        self.main_text: list[str] = []
        self.all_text: list[str] = []
        self.h1_in_main = False

    def inside(self, name: str) -> bool:
        return any(t == name for t, _ in self.stack)

    def hidden(self) -> bool:
        return any(h for _, h in self.stack)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "html":
            self.lang = a.get("lang") or ""
        in_head = self.inside("head")
        if tag == "meta" and in_head:
            name = (a.get("name") or "").lower()
            if name == "description":
                self.description = a.get("content") or ""
            elif name in ("robots", "googlebot", "bingbot"):
                self.robots.append((a.get("content") or "").lower())
        if tag == "link":
            rel = (a.get("rel") or "").lower()
            if rel == "canonical":
                if in_head:
                    self.canonicals.append(a.get("href") or "")
                else:
                    self.body_canonicals += 1
            elif rel == "alternate" and a.get("hreflang"):
                self.alternates.append((a["hreflang"], a.get("href") or ""))
        if tag == "a" and a.get("href"):
            self.links.append(a["href"])
        if tag == "h1" and self.inside("main") and not self.hidden() and "hidden" not in a:
            self.h1_in_main = True
        if tag not in VOID:
            self.stack.append((tag, "hidden" in a or tag in ("noscript", "template")))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        if not self.stack:
            return
        top = self.stack[-1][0]
        if top == "title" and self.inside("head"):
            self.title += data
        elif top == "script":
            pass
        elif top != "style" and not self.inside("head") and not self.hidden():
            self.all_text.append(data)
            if self.inside("main"):
                self.main_text.append(data)

    def script_blocks(self, raw: str) -> list[str]:
        # HTMLParser hands script bodies over as data with no type; a small scan of the raw page is exact for this.
        return re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', raw, re.S)


def norm(text: str) -> str:
    return " ".join(text.split()).casefold()


class Crawl:
    def __init__(self, base: str, origin: str, key: str, data_dir: Path = ROOT / "data", browser: bool = True) -> None:
        self.base, self.origin, self.key = base.rstrip("/"), origin.rstrip("/"), key
        self.data_dir, self.browser = data_dir, browser
        self.errors: list[str] = []
        self.lock = threading.Lock()

    def error(self, where: str, message: str) -> None:
        with self.lock:
            self.errors.append(f"{where}: {message}")

    def fetch(self, path: str):
        request = urllib.request.Request(self.base + path, headers={"User-Agent": "invoicesnap-check-site/1"})
        try:
            with OPENER.open(request, timeout=60) as response:
                return response.status, response.read().decode("utf-8", "replace"), response.headers  # Not dict(): names must stay case-insensitive, http.server sends "Content-type".
        except urllib.error.HTTPError as e:
            return e.code, "", e.headers
        except (OSError, http.client.HTTPException) as e:
            return 0, str(e), http.client.HTTPMessage()  # Status 0 = could not look. It is reported, never counted as empty.

    def to_path(self, url: str) -> str | None:
        return url[len(self.origin):] if url.startswith(self.origin + "/") else None

    def read_sitemaps(self) -> dict[str, str]:
        """URL path -> sitemap file name."""
        status, text, _ = self.fetch("/sitemap.xml")
        if status != 200:
            self.error("/sitemap.xml", f"HTTP {status}")
            return {}
        try:
            root = ET.fromstring(text)
        except ET.ParseError as e:
            self.error("/sitemap.xml", f"not valid XML: {e}")
            return {}
        if root.tag != "{%s}sitemapindex" % NS["s"]:
            self.error("/sitemap.xml", "is not a sitemap index")
            return {}
        pages: dict[str, str] = {}
        for loc in root.findall("s:sitemap/s:loc", NS):
            path = self.to_path(loc.text or "")
            if not path or not re.fullmatch(r"/sitemap-[a-z-]+\.xml", path):
                self.error("/sitemap.xml", f"unexpected child sitemap {loc.text}")
                continue
            status, text, _ = self.fetch(path)
            if status != 200:
                self.error(path, f"HTTP {status}")
                continue
            try:
                child = ET.fromstring(text)
            except ET.ParseError as e:
                self.error(path, f"not valid XML: {e}")
                continue
            if child.tag != "{%s}urlset" % NS["s"] or not child.findall("s:url", NS):
                self.error(path, "is not a urlset with at least one URL")
            for url in child.findall("s:url", NS):
                page = self.to_path(url.findtext("s:loc", "", NS))
                if not page:
                    self.error(path, f"URL outside the site: {url.findtext('s:loc', '', NS)}")
                    continue
                if page in pages:
                    self.error(path, f"{page} is also in {pages[page]}")
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", url.findtext("s:lastmod", "", NS)):
                    self.error(path, f"{page}: lastmod is not a date")
                pages[page] = path
        if not pages:
            self.error("/sitemap.xml", "no pages found")
        return pages

    def check_page(self, path: str, sitemap: str) -> dict | None:
        status, raw, headers = self.fetch(path)
        if status != 200:
            self.error(path, f"HTTP {status}, expected 200" + (f" (redirect to {headers.get('Location')})" if headers.get("Location") else ""))
            return None
        if "noindex" in (headers.get("X-Robots-Tag") or "").lower():
            self.error(path, "X-Robots-Tag has noindex")
        page = Page()
        page.feed(raw)
        page.close()
        url = self.origin + path
        if page.canonicals != [url]:
            self.error(path, f"canonical in <head> is {page.canonicals}, expected [{url}]")
        if page.body_canonicals:
            self.error(path, "canonical outside <head>")
        if any("noindex" in r or "nosnippet" in r for r in page.robots):
            self.error(path, f"robots meta blocks the page: {page.robots}")
        if not page.title.strip():
            self.error(path, "title is empty")
        if not page.description.strip():
            self.error(path, "meta description is empty")
        if not page.h1_in_main:
            self.error(path, "no visible <h1> inside <main>")
        main_text = " ".join(" ".join(page.main_text).split())
        if len(main_text) < MIN_TEXT:
            self.error(path, f"<main> has {len(main_text)} characters of server-rendered text, expected at least {MIN_TEXT}")
        if sitemap != f"/sitemap-{page.lang.lower()}.xml":
            self.error(path, f"lang {page.lang} is listed in {sitemap}")
        alternates = dict(page.alternates)
        if len(alternates) != len(page.alternates):
            self.error(path, "duplicate hreflang tag")
        for tag in alternates:
            if not HREFLANG.match(tag):
                self.error(path, f"bad hreflang tag {tag}")
        if alternates.get(page.lang) != url:
            self.error(path, f"no self hreflang: lang {page.lang} points to {alternates.get(page.lang)}")
        if "x-default" not in alternates:
            self.error(path, "no x-default hreflang")
        self.check_schema(path, url, page, raw)
        return {"title": page.title.strip(), "description": page.description.strip(), "lang": page.lang,
                "alternates": alternates, "links": page.links}

    def check_schema(self, path: str, url: str, page: Page, raw: str) -> None:
        blocks = page.script_blocks(raw)
        if not blocks:
            self.error(path, "no JSON-LD schema")
        visible = norm(" ".join(page.all_text))
        for block in blocks:
            try:
                data = json.loads(block)
            except json.JSONDecodeError as e:
                self.error(path, f"JSON-LD does not parse: {e}")
                continue
            for node in data.get("@graph", [data]):
                kind = node.get("@type")
                if kind == "WebPage":
                    if node.get("url") != url:
                        self.error(path, f"WebPage url is {node.get('url')}")
                    if norm(node.get("name", "")) != norm(page.title):
                        self.error(path, f"WebPage name {node.get('name')!r} differs from the title")
                    if norm(node.get("description", "")) != norm(page.description):
                        self.error(path, "WebPage description differs from the meta description")
                elif kind == "FAQPage":
                    for item in node.get("mainEntity", []):
                        for text in (item.get("name", ""), item.get("acceptedAnswer", {}).get("text", "")):
                            if norm(text) not in visible:
                                self.error(path, f"FAQ schema text is not on the page: {text[:60]!r}")

    def check_robots(self, sample: list[str]) -> None:
        status, text, _ = self.fetch("/robots.txt")
        if status != 200:
            self.error("/robots.txt", f"HTTP {status}")
            return
        if f"Sitemap: {self.origin}/sitemap.xml" not in text:
            self.error("/robots.txt", "does not name the sitemap")
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(text.splitlines())
        for robot in ROBOTS:
            for path in sample:
                if not parser.can_fetch(robot, self.origin + path):
                    self.error("/robots.txt", f"{robot} is blocked from {path}")

    def run(self) -> int:
        pages = self.read_sitemaps()
        with ThreadPoolExecutor(16) as pool:
            results = dict(zip(pages, pool.map(lambda p: self.check_page(p, pages[p]), pages)))
        seen: dict[tuple[str, str], str] = {}
        for path, result in results.items():
            if not result:
                continue
            for label in ("title", "description"):
                key = (result["lang"], label + result[label])
                if key in seen:
                    self.error(path, f"same {label} as {seen[key]}")
                seen.setdefault(key, path)
            for tag, target in result["alternates"].items():
                target_path = self.to_path(target)
                if target_path not in results:
                    self.error(path, f"hreflang {tag} points to {target}, which is not in a sitemap")
                elif results[target_path] and results[target_path]["alternates"] != result["alternates"]:
                    self.error(path, f"hreflang set differs on {target_path}: the pair is not reciprocal")
        self.check_links(results)
        self.check_robots(list(pages))
        self.check_tool_gate(results)
        status, text, _ = self.fetch(f"/{self.key}.txt")
        if status != 200 or text.strip() != self.key:
            self.error(f"/{self.key}.txt", f"IndexNow key file: HTTP {status}")
        for line in sorted(self.errors)[:2000]:  # A high cap: one broken page can repeat in every sibling of its cluster.
            print(f"FAIL {line}")
        print(f"checked {len(results)} pages in {len(set(pages.values()))} language sitemaps: {len(self.errors)} problem(s)")
        return 1 if self.errors else 0

    def check_tool_gate(self, results: dict) -> None:
        ctx = tool_pages.Context(self.data_dir)
        if not ctx.pages:
            return
        today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
        fetched, gated, texts, h1s = {}, [], {}, {}
        jobs = gate.filled_job_problems(ctx.pages)
        path_of = lambda p: ("" if p.get("locale", "en") == "en" else f"/{p['locale']}") + "/" + p["slug"].strip("/") + "/"  # noqa: E731
        for page in ctx.pages:
            path = path_of(page)
            status, raw, _ = self.fetch(path)
            problems = gate.data_problems(page, ctx.sources, ctx.demand, ctx.laws, "en-US", today) + jobs.get(page["id"], [])
            if path not in results:
                # Gate says fail = noindex or no build. A page outside the sitemap must really carry noindex.
                if status == 200 and "noindex" not in gate.parse(raw).robots:
                    self.error(path, "gate: the page is outside the sitemap but has no noindex")
                print(f"gate: {path} is blocked, noindex: {'; '.join(problems)[:200]}")
                if not problems:  # A green page must be live: without this, a build bug could hide pages from search with no failure.
                    self.error(path, "gate: the page passes every data gate but is not in a sitemap")
                continue
            if status != 200:
                continue
            gated.append(path)
            fetched[path] = raw
            problems += gate.html_problems(page, raw, ctx.claims)
            parsed = gate.parse(raw)
            texts[path] = " ".join(parsed.unique)
            other = h1s.setdefault(gate.norm(parsed.h1), path)
            if other != path:
                problems.append(f"gate 1: same h1 as {other}")
            for line in problems:
                self.error(path, line)
        for path, problems in gate.overlap_problems(texts).items():
            for line in problems:
                self.error(path, line)
        share, a, b = gate.max_overlap(texts)
        print(f"gate 6: highest 5-word overlap {share:.2f} ({a} / {b})")
        for line in gate.claims_problems(ctx.claims, APP_REPO):
            self.error("/claims", line)  # "unknown" (no app repo here) also stops a release: a failed look is not a pass.
        if self.browser:
            forms = [path_of(p) for p in ctx.pages if p["type"] in tool_pages.FORM_TYPES]
            forms = [p for p in forms if p in gated]
            for path, problems in gate_browser.run(self.base, forms).items():
                for line in problems:
                    self.error(path, line)
            print(f"gate 2, 3: browser checked {len(forms)} form pages")

    def check_links(self, results: dict) -> None:
        internal: dict[str, str] = {}
        for path, result in results.items():
            for href in (result or {}).get("links", []):
                target = urllib.parse.urlsplit(urllib.parse.urljoin(self.origin + path, href))
                if target.scheme not in ("http", "https") or f"{target.scheme}://{target.netloc}" != self.origin:
                    continue
                if target.query:
                    self.error(path, f"internal link has a query string: {href}")
                internal.setdefault(target.path or "/", path)
        for target, source in internal.items():
            if target in results:
                continue
            status, _, headers = self.fetch(target)
            if status != 200:
                self.error(source, f"link to {target}: HTTP {status}")
            elif (headers.get("Content-Type") or "").startswith("text/html"):
                self.error(source, f"link to {target}: the page is not in a sitemap")


def serve(directory: Path) -> tuple[http.server.ThreadingHTTPServer, str]:
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    handler = functools.partial(Quiet, directory=str(directory))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--dir", type=Path, help="serve this folder locally and crawl it")
    source.add_argument("--base", help="crawl this live base URL")
    parser.add_argument("--data", type=Path, default=ROOT / "data", help="data folder with site.json")
    parser.add_argument("--no-browser", action="store_true", help="skip gates 2 and 3 (test fixtures only; publish.py never passes it)")
    args = parser.parse_args()
    config = json.loads((args.data / "site.json").read_text(encoding="utf-8"))
    server = None
    base = args.base
    if args.dir:
        server, base = serve(args.dir)
    try:
        return Crawl(base, config["origin"], config["indexnow_key"], args.data, not args.no_browser).run()
    finally:
        if server:
            server.shutdown()


if __name__ == "__main__":
    sys.exit(main())
