#!/usr/bin/env python3
"""Build invoicesnap.work into dist/ from data (NIK-545, 8 Nov 2026). Python standard library only.

Usage:
  python3 build.py            build dist/ and update data/lastmod.json. Commit that file.
  python3 build.py --check    build dist/ but fail if data/lastmod.json is out of date.
  python3 check_site.py --dir dist    crawl the build over real HTTP and check every SEO rule.
  python3 publish.py          deploy to production (see its header).

Add a page: write data/pages/<id>.json, one file for one page in all its languages.
  {"id": "plumber-invoice", "page_type": "guide",
   "locales": {
     "en": {"slug": "plumber-invoice-template", "title": "...", "description": "...", "h1": "...",
            "body_html": "<p>Text rendered by the server.</p>",
            "faq": [{"q": "...", "a": "..."}],
            "schema": [{"@type": "HowTo", "name": "..."}]},
     "de": {"slug": "rechnung-klempner", "title": "...", ...}}}
  URL = /<locale prefix from data/locales.json>/<slug>/. Each locale may have its own slug, in ASCII: a-z, 0-9, hyphen
  (write native words in Latin letters). The build stops on other characters.
  The build makes the canonical, the hreflang pairs and x-default, the WebPage schema, the FAQPage schema
  (from "faq", shown on the page too) and the language sitemap. "en" is required: it is x-default.
  "faq" and "schema" are optional. Any value in "schema" must match the visible text (playbook rule R12).
  A locale other than en needs a "ui" block in data/locales.json; the build stops and says so.
  "source" instead of the body fields: the page is a whole HTML file under src/pages/ (see data/pages/home.json).
  Static files (images, tool scripts) go under src/static/ and are copied as they are.

Facts that are not obvious:
- Sitemaps stay at the site root: a sitemap may list only URLs at or below its own folder, so
  /sitemaps/en.xml could not list /privacy/.
- lastmod changes only when the <main> text, title or description changes (playbook rule R03). A template, CSS or
  footer change does not move it, so a new locale in the language list does not move the sibling pages. Two builds give the same date. data/lastmod.json keeps hash and date.
- data/locales.json follows the app bundles in invoicesnap-expo src/i18n/locales ("app_locale" names the bundle).
  translations/ in that repo is deprecated: do not use it as the list. Bundle en-US is the root locale "en", which
  is also x-default; en-GB, en-CA and en-AU are separate URL sets.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import re
import shutil
import sys
from html.parser import HTMLParser
from pathlib import Path
from string import Template
from xml.sax.saxutils import escape as xml_escape

ROOT = Path(__file__).resolve().parent
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
TITLE_MAX, DESCRIPTION_MAX = 70, 170  # Longer text is cut in search results: a warning, not an error.


class BuildError(Exception):
    pass


class PageText(HTMLParser):
    """Visible text and the head values of one HTML page. A real parser, because rule R01 forbids regex checks."""

    SKIP = {"script", "style", "template", "noscript"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0
        self.in_head = False
        self.in_title = False
        self.title = ""
        self.description = ""
        self.canonical = ""
        self.lang = ""
        self.main_depth = 0
        self.main_parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "html":
            self.lang = a.get("lang", "")
        elif tag == "head":
            self.in_head = True
        elif tag == "title" and self.in_head:
            self.in_title = True
        elif tag == "meta" and a.get("name") == "description":
            self.description = a.get("content", "")
        elif tag == "link" and a.get("rel") == "canonical":
            self.canonical = a.get("href", "")
        if tag == "main" or (tag not in VOID and self.main_depth):
            self.main_depth += 1
        if tag in self.SKIP:
            self.skip += 1

    def handle_endtag(self, tag):
        if self.main_depth and tag not in VOID:
            self.main_depth -= 1
        if tag == "head":
            self.in_head = False
        elif tag == "title":
            self.in_title = False
        if tag in self.SKIP and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        elif not self.skip and not self.in_head:
            self.parts.append(data)
            if self.main_depth:
                self.main_parts.append(data)

    def main_text(self) -> str:
        return " ".join(" ".join(self.main_parts).split())


def parse(page_html: str) -> PageText:
    parser = PageText()
    parser.feed(page_html)
    parser.close()
    return parser


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise BuildError(f"{path}: not valid JSON: {e}") from e


def json_ld(graph: list[dict]) -> str:
    # "<" is escaped so that no text value can close the script tag.
    text = json.dumps({"@context": "https://schema.org", "@graph": graph}, ensure_ascii=False).replace("<", "\\u003c")
    return f'<script type="application/ld+json">{text}</script>'


class Site:
    def __init__(self, data_dir: Path, out: Path, today: str, check: bool) -> None:
        self.data_dir, self.out, self.today, self.check = data_dir, out, today, check
        self.config = load_json(data_dir / "site.json")
        self.origin = self.config["origin"]
        self.locales = load_json(data_dir / "locales.json")
        self.lock_path = data_dir / "lastmod.json"
        self.lock = load_json(self.lock_path) if self.lock_path.exists() else {}
        self.templates: dict[str, Template] = {}
        self.warnings: list[str] = []

    def url_path(self, tag: str, slug: str) -> str:
        if tag not in self.locales:
            raise BuildError(f"locale {tag} is not in data/locales.json")
        parts = [p for p in (self.locales[tag]["prefix"], slug.strip("/")) if p]
        return "/" + "".join(f"{p}/" for p in parts)

    def read_pages(self) -> list[dict]:
        pages = [load_json(f) for f in sorted((self.data_dir / "pages").glob("*.json"))]
        seen: dict[str, str] = {}
        for page in pages:
            for key in ("id", "page_type", "locales"):
                if key not in page:
                    raise BuildError(f"page {page.get('id', '?')}: field {key} is required")
            default = self.config["default_locale"]
            if default not in page["locales"]:
                raise BuildError(f"page {page['id']}: locale {default} is required, it is the x-default target")
            for tag, entry in page["locales"].items():
                if "slug" not in entry:
                    raise BuildError(f"page {page['id']} {tag}: field slug is required")
                path = self.url_path(tag, entry["slug"])
                if not re.fullmatch(r"/(?:[a-z0-9-]+/)*", path):
                    raise BuildError(f"page {page['id']} {tag}: bad URL {path}: use lower case ASCII a-z, 0-9 and hyphen")
                if path in seen:
                    raise BuildError(f"URL {path} is used by pages {seen[path]} and {page['id']}")
                seen[path] = page["id"]
        return pages

    def alternates(self, page: dict) -> dict[str, str]:
        result = {tag: self.origin + self.url_path(tag, e["slug"]) for tag, e in page["locales"].items()}
        result["x-default"] = result[self.config["default_locale"]]
        return result

    def seo_links(self, alternates: dict[str, str]) -> str:
        return "\n  ".join(
            f'<link rel="alternate" hreflang="{tag}" href="{html.escape(url)}">' for tag, url in alternates.items()
        )

    def web_page_node(self, url: str, tag: str, title: str, description: str) -> dict:
        return {
            "@type": "WebPage", "@id": f"{url}#webpage", "url": url, "name": title, "description": description,
            "inLanguage": tag,
            "isPartOf": {"@type": "WebSite", "@id": f"{self.origin}/#website", "url": f"{self.origin}/", "name": "InvoiceSnap"},
            "about": {"@id": f"{self.origin}/#software"},
        }

    def render_source(self, page: dict, tag: str, entry: dict, url: str, alternates: dict[str, str]) -> str:
        """A whole HTML file: its head must agree with the data; the build adds only hreflang and missing schema."""
        path = ROOT / "src" / "pages" / entry["source"]
        text = path.read_text(encoding="utf-8")
        parsed = parse(text)
        where = f"{path.relative_to(ROOT)}"
        if parsed.lang != tag:
            raise BuildError(f"{where}: <html lang> is {parsed.lang!r}, expected {tag!r}")
        if parsed.canonical != url:
            raise BuildError(f"{where}: canonical is {parsed.canonical!r}, expected {url!r}")
        if not parsed.title.strip() or not parsed.description.strip():
            raise BuildError(f"{where}: title and meta description are required")
        block = [self.seo_links(alternates)]
        if "application/ld+json" not in text:
            block.append(json_ld([self.web_page_node(url, tag, html.unescape(parsed.title).strip(), parsed.description)]))
        if text.count("</head>") != 1:
            raise BuildError(f"{where}: expected exactly one </head>")
        return text.replace("</head>", "  " + "\n  ".join(block) + "\n</head>")

    def render_template(self, page: dict, tag: str, entry: dict, url: str, alternates: dict[str, str]) -> str:
        for key in ("title", "description", "h1", "body_html"):
            if not entry.get(key):
                raise BuildError(f"page {page['id']} {tag}: field {key} is required (or give source)")
        ui = self.locales[tag].get("ui")
        if not ui:
            raise BuildError(f"page {page['id']} {tag}: add a \"ui\" block for {tag} in data/locales.json")
        name = page.get("template", "page")
        if name not in self.templates:
            self.templates[name] = Template((ROOT / "templates" / f"{name}.html").read_text(encoding="utf-8"))
        graph = [self.web_page_node(url, tag, entry["title"], entry["description"])]
        faq_html = ""
        if entry.get("faq"):
            # The same values go to the page and to the schema, so they cannot differ (rule R12).
            graph.append({"@type": "FAQPage", "@id": f"{url}#faq", "mainEntity": [
                {"@type": "Question", "name": q["q"], "acceptedAnswer": {"@type": "Answer", "text": q["a"]}}
                for q in entry["faq"]]})
            faq_html = "".join(f"<h2>{html.escape(q['q'])}</h2><p>{html.escape(q['a'])}</p>" for q in entry["faq"])
        graph.extend(entry.get("schema", []))
        languages = ""
        if len(page["locales"]) > 1:
            links = "".join(
                f'<li><a lang="{t}" hreflang="{t}" href="{html.escape(alternates[t])}">{html.escape(self.locales[t]["name"])}</a></li>'
                for t in page["locales"])
            languages = f'<details class="languages"><summary>{html.escape(ui["languages"])}</summary><ul>{links}</ul></details>'
        return self.templates[name].substitute(
            lang=tag, dir=self.locales[tag]["dir"], title=html.escape(entry["title"]),
            description=html.escape(entry["description"]), canonical=html.escape(url), h1=html.escape(entry["h1"]),
            seo_block=self.seo_links(alternates) + "\n  " + json_ld(graph), body=entry["body_html"], faq=faq_html,
            languages=languages, **{f"ui_{k}": html.escape(v) for k, v in ui.items()})

    def lastmod(self, path: str, rendered: str, stale: list[str]) -> str:
        parsed = parse(rendered)
        digest = hashlib.sha256("\n".join((parsed.title.strip(), parsed.description.strip(), parsed.main_text())).encode()).hexdigest()
        known = self.lock.get(path)
        if known and known["hash"] == digest:
            return known["lastmod"]
        stale.append(path)
        self.lock[path] = {"hash": digest, "lastmod": self.today}
        return self.today

    def write_sitemaps(self, by_locale: dict[str, list[tuple[str, str]]]) -> None:
        entries = []
        for tag in sorted(by_locale):
            rows = sorted(by_locale[tag])
            name = f"sitemap-{self.locales[tag]['prefix'] or tag.lower()}.xml"
            body = "".join(f"  <url><loc>{xml_escape(self.origin + p)}</loc><lastmod>{m}</lastmod></url>\n" for p, m in rows)
            (self.out / name).write_text(
                '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                f"{body}</urlset>\n", encoding="utf-8")
            entries.append(f"  <sitemap><loc>{self.origin}/{name}</loc><lastmod>{max(m for _, m in rows)}</lastmod></sitemap>\n")
        (self.out / "sitemap.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "".join(entries) + "</sitemapindex>\n", encoding="utf-8")

    def build(self) -> int:
        pages = self.read_pages()
        if self.out.exists():
            shutil.rmtree(self.out)
        shutil.copytree(ROOT / "src" / "static", self.out)
        key = self.config["indexnow_key"]
        (self.out / f"{key}.txt").write_text(key, encoding="ascii")
        by_locale: dict[str, list[tuple[str, str]]] = {}
        stale: list[str] = []
        titles: dict[tuple[str, str], str] = {}
        live_paths = set()
        for page in pages:
            alternates = self.alternates(page)
            for tag, entry in page["locales"].items():
                path = self.url_path(tag, entry["slug"])
                url = self.origin + path
                render = self.render_source if "source" in entry else self.render_template
                rendered = render(page, tag, entry, url, alternates)
                parsed = parse(rendered)
                for label, value in (("title", parsed.title.strip()), ("description", parsed.description.strip())):
                    other = titles.setdefault((tag, label + value), path)
                    if other != path:
                        raise BuildError(f"{path} and {other} have the same {label} in {tag}: {value[:60]!r}")
                if len(parsed.title.strip()) > TITLE_MAX:
                    self.warnings.append(f"{path}: title has {len(parsed.title.strip())} characters, keep it under {TITLE_MAX}")
                if len(parsed.description.strip()) > DESCRIPTION_MAX:
                    self.warnings.append(f"{path}: description has {len(parsed.description.strip())} characters, keep it under {DESCRIPTION_MAX}")
                target = self.out / path.lstrip("/") / "index.html"
                if target.exists():
                    raise BuildError(f"{path}: a static file in src/static already uses this URL")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(rendered, encoding="utf-8")
                by_locale.setdefault(tag, []).append((path, self.lastmod(path, rendered, stale)))
                live_paths.add(path)
        removed = sorted(set(self.lock) - live_paths)
        for path in removed:
            del self.lock[path]
        self.write_sitemaps(by_locale)
        for warning in self.warnings:
            print(f"warning: {warning}", file=sys.stderr)
        if self.check and (stale or removed):
            raise BuildError(f"data/lastmod.json is out of date for {len(stale) + len(removed)} URL(s), first: "
                             f"{(stale + removed)[0]}. Run: python3 build.py, then commit data/lastmod.json")
        if not self.check:
            self.lock_path.write_text(json.dumps(dict(sorted(self.lock.items())), indent=0) + "\n", encoding="utf-8")
        print(f"built {sum(len(v) for v in by_locale.values())} pages in {len(by_locale)} locales -> {self.out}")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="fail if data/lastmod.json is out of date; do not change it")
    parser.add_argument("--data", type=Path, default=ROOT / "data", help="data folder (tests use a fixture)")
    parser.add_argument("--out", type=Path, default=ROOT / "dist", help="output folder")
    parser.add_argument("--today", default=dt.datetime.now(dt.timezone.utc).date().isoformat(), help="date for changed pages")
    args = parser.parse_args()
    try:
        return Site(args.data, args.out, args.today, args.check).build()
    except BuildError as e:
        print(f"build failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
