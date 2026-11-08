#!/usr/bin/env python3
"""End-to-end test of build.py + check_site.py at the real boundary: real files, real local HTTP (NIK-545, 8 Nov 2026).

Usage: python3 tests/test_site.py

1. Builds a fixture of the real 4 pages + 36 pages x 39 locales, crawls it, and expects 0 problems.
2. Proves lastmod is stable, and that only a page with changed text moves.
3. Breaks the build output in each way a search rule forbids, and expects check_site.py to fail with that reason.
   If a check cannot fail, it is decorative, so this part is the proof that a green crawl means something.
4. Proves build.py stops on bad data and says the fix.
No mocks. Fixture text is throwaway; it never goes live.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGES, LOCALES = 36, 39
failures: list[str] = []


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *args], capture_output=True, text=True, timeout=600)


def expect(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f": {detail[:400]}" if not ok else ""))
    if not ok:
        failures.append(name)


def make_data(folder: Path) -> Path:
    data = folder / "data"
    shutil.copytree(ROOT / "data", data)
    locales = json.loads((data / "locales.json").read_text(encoding="utf-8"))
    ui = locales["en"]["ui"]
    for value in locales.values():
        value["ui"] = ui
    (data / "locales.json").write_text(json.dumps(locales, ensure_ascii=False), encoding="utf-8")
    for n in range(PAGES):
        page = {"id": f"fixture-{n}", "page_type": "guide", "locales": {}}
        for tag in locales:
            filler = " ".join(f"Line {i} of page {n} in {tag} gives one plain fact." for i in range(8))
            page["locales"][tag] = {
                "slug": f"fixture-{n}", "title": f"Fixture {n} {tag}", "description": f"Fixture page {n} in locale {tag}.",
                "h1": f"Fixture {n} <{tag}>", "body_html": f"<p>{filler}</p>",
                "faq": [{"q": f"Question {n} in {tag}?", "a": f"Answer {n} & more in {tag}."}]}
        (data / "pages" / f"fixture-{n}.json").write_text(json.dumps(page), encoding="utf-8")
    return data


def build(data: Path, out: Path, *extra: str) -> subprocess.CompletedProcess:
    return run(str(ROOT / "build.py"), "--data", str(data), "--out", str(out), *extra)


def crawl(data: Path, out: Path) -> subprocess.CompletedProcess:
    return run(str(ROOT / "check_site.py"), "--dir", str(out), "--data", str(data))


def lastmods(out: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for file in out.glob("sitemap-*.xml"):
        result.update(re.findall(r"<loc>(.*?)</loc><lastmod>(.*?)</lastmod>", file.read_text(encoding="utf-8")))
    return result


def sub(path: Path, old: str, new: str, count: int = 1) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{old!r} is not in {path}"
    path.write_text(text.replace(old, new, count), encoding="utf-8")


def mutations(out: Path) -> list[tuple[str, str, object]]:
    de = out / "de/fixture-0/index.html"
    fr = out / "fr/fixture-1/index.html"
    return [
        ("hreflang pair not reciprocal", "not reciprocal",
         lambda: sub(de, '<link rel="alternate" hreflang="fr" href="https://invoicesnap.work/fr/fixture-0/">\n', "")),
        ("no self hreflang", "no self hreflang",
         lambda: sub(de, '<link rel="alternate" hreflang="de" href="https://invoicesnap.work/de/fixture-0/">\n', "")),
        ("no x-default", "no x-default",
         lambda: sub(fr, '<link rel="alternate" hreflang="x-default" href="https://invoicesnap.work/fixture-1/">\n', "")),
        ("bad hreflang tag", "bad hreflang tag", lambda: sub(de, 'hreflang="ja"', 'hreflang="japanese"')),
        ("noindex", "robots meta blocks", lambda: sub(de, "</head>", '<meta name="robots" content="noindex"></head>')),
        ("wrong canonical", "canonical in <head>",
         lambda: sub(de, 'rel="canonical" href="https://invoicesnap.work/de/fixture-0/"', 'rel="canonical" href="https://invoicesnap.work/"')),
        ("canonical in body", "canonical outside", lambda: sub(de, "</main>", '<link rel="canonical" href="/x"></main>')),
        ("page is gone", "HTTP 404", lambda: shutil.rmtree(de.parent)),  # Whole folder: the local server lists an empty folder with 200.
        ("text only from script", "characters of server-rendered text",
         lambda: de.write_text(re.sub(r"<p>.*?</main>", "</main>", de.read_text(encoding="utf-8"), flags=re.S), encoding="utf-8")),
        ("h1 outside main", "no visible <h1>", lambda: sub(de, "<main>\n    <h1>", "<h1>x</h1><main>\n    <h2>")),
        ("broken JSON-LD", "does not parse", lambda: sub(de, '"@graph"', '"@graph" oops')),
        ("no JSON-LD", "no JSON-LD", lambda: de.write_text(re.sub(r'<script type="application/ld\+json">.*?</script>', "", de.read_text(encoding="utf-8"), flags=re.S), encoding="utf-8")),
        ("FAQ schema differs from page", "FAQ schema text", lambda: sub(de, "<h2>Question 0 in de?</h2>", "<h2>Other text</h2>")),
        ("WebPage name differs", "WebPage name", lambda: sub(de, '"name": "Fixture 0 de"', '"name": "Other"')),
        ("duplicate title", "same title", lambda: sub(de, "<title>Fixture 0 de</title>", "<title>Fixture 1 de</title>")),
        ("URL missing from sitemap", "not in a sitemap", lambda: sub(out / "sitemap-de.xml", "/de/fixture-0/", "/de/fixture-0-nope/")),
        ("URL in wrong sitemap", "is listed in", lambda: sub(out / "sitemap-fr.xml", "/fr/fixture-1/", "/de/fixture-1/") or sub(out / "sitemap-de.xml", "  <url><loc>https://invoicesnap.work/de/fixture-1/</loc><lastmod>2026-11-08</lastmod></url>\n", "")),
        ("sitemap is not an index", "not a sitemap index", lambda: shutil.copy(out / "sitemap-en.xml", out / "sitemap.xml")),
        ("internal link with query", "query string", lambda: sub(de, 'href="/privacy/"', 'href="/privacy/?utm_source=x"')),
        ("broken internal link", "HTTP 404", lambda: sub(de, 'href="/privacy/"', 'href="/nope/"')),
        ("robots blocks a search robot", "is blocked from", lambda: (out / "robots.txt").write_text("User-agent: OAI-SearchBot\nDisallow: /\n\nSitemap: https://invoicesnap.work/sitemap.xml\n")),
        ("robots has no sitemap line", "does not name the sitemap", lambda: (out / "robots.txt").write_text("User-agent: *\nAllow: /\n")),
        ("IndexNow key file is gone", "IndexNow key file", lambda: next(out.glob("*.txt")).unlink()),
    ]


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        data, out = make_data(tmp_path), tmp_path / "dist"
        # 1. full scale
        started = time.time()
        result = build(data, out, "--today", "2026-11-08")
        expect("fixture builds", result.returncode == 0, result.stderr)
        built = time.time() - started
        started = time.time()
        result = crawl(data, out)
        expect("fixture crawl is clean", result.returncode == 0, result.stdout[-1500:])
        total = PAGES * LOCALES + 4
        expect(f"crawl saw {total} pages in {LOCALES} language sitemaps", f"checked {total} pages in {LOCALES} language sitemaps" in result.stdout, result.stdout[-300:])
        print(f"     build {built:.1f} s, crawl {time.time() - started:.1f} s")
        index = (out / "sitemap.xml").read_text(encoding="utf-8")
        expect("sitemap index lists zh-hans", "sitemap-zh-hans.xml" in index, index[:300])
        arabic = (out / "ar/fixture-0/index.html").read_text(encoding="utf-8")
        expect("Arabic page is right to left", '<html lang="ar" dir="rtl">' in arabic, arabic[:200])
        expect("page text is escaped", "Fixture 0 &lt;ar&gt;" in arabic and "&amp; more" in arabic, "")
        expect("home has its hreflang", 'hreflang="x-default" href="https://invoicesnap.work/"' in (out / "index.html").read_text(encoding="utf-8"), "")

        # 2. lastmod
        first = lastmods(out)
        result = build(data, out, "--today", "2026-12-25", "--check")
        expect("second build with no change: --check passes on a later day", result.returncode == 0, result.stderr)
        expect("second build keeps every lastmod", lastmods(out) == first, "")
        expect("old page keeps its real date", first["https://invoicesnap.work/privacy/"] == "2026-09-28", str(first.get("https://invoicesnap.work/privacy/")))
        page_file = data / "pages" / "fixture-3.json"
        original = page_file.read_text(encoding="utf-8")
        page_file.write_text(original.replace("Line 2 of page 3 in de", "Line 2 of page 3 in DE changed"), encoding="utf-8")
        result = build(data, out, "--today", "2026-12-25", "--check")
        expect("--check fails on changed text and says the fix", result.returncode == 1 and "Run: python3 build.py" in result.stderr, result.stderr)
        build(data, out, "--today", "2026-12-25")
        moved = {u for u, m in lastmods(out).items() if first.get(u) != m}
        expect("only the changed page moves", moved == {"https://invoicesnap.work/de/fixture-3/"}, str(sorted(moved))[:300])
        page_file.write_text(original, encoding="utf-8")
        build(data, out, "--today", "2026-11-08")

        # 3. each check can fail
        pristine = tmp_path / "pristine"
        shutil.copytree(out, pristine)
        for index_number in range(len(mutations(out))):
            shutil.rmtree(out)
            shutil.copytree(pristine, out)
            name, reason, change = mutations(out)[index_number]
            change()
            result = crawl(data, out)
            expect(f"check fails: {name}", result.returncode == 1 and reason in result.stdout, f"exit {result.returncode}; {result.stdout[-400:]}")

        # 4. bad data stops the build
        def bad(name: str, reason: str, file: str, edit) -> None:
            path = data / file
            before = path.read_text(encoding="utf-8")
            value = json.loads(before)
            edit(value)
            path.write_text(json.dumps(value), encoding="utf-8")
            result = build(data, tmp_path / "bad-out")
            path.write_text(before, encoding="utf-8")
            expect(f"build stops: {name}", result.returncode == 1 and reason in result.stderr, result.stderr)

        bad("no en", "locale en is required", "pages/fixture-0.json", lambda v: v["locales"].pop("en"))
        bad("unknown locale", "is not in data/locales.json", "pages/fixture-0.json", lambda v: v["locales"].update({"xx": v["locales"]["de"]}))
        bad("same URL twice", "is used by pages", "pages/fixture-0.json", lambda v: v["locales"]["de"].update({"slug": "fixture-0/../fixture-1"}) or v["locales"]["de"].update({"slug": "fixture-1"}))
        bad("upper case slug", "use lower case", "pages/fixture-0.json", lambda v: v["locales"]["de"].update({"slug": "Fixture-0x"}))
        bad("no title", "field title is required", "pages/fixture-0.json", lambda v: v["locales"]["de"].pop("title"))
        bad("no ui for the locale", 'add a "ui" block for de', "locales.json", lambda v: v["de"].pop("ui"))
        bad("same title twice", "have the same title", "pages/fixture-0.json", lambda v: v["locales"]["de"].update({"title": "Fixture 1 de"}))
        bad("source canonical differs", "canonical is", "pages/home.json", lambda v: v["locales"]["en"].update({"slug": "other"}))

        # The real data must also be clean, with the committed lastmod file.
        real_out = tmp_path / "real"
        result = run(str(ROOT / "build.py"), "--check", "--out", str(real_out))
        expect("real data: build --check passes", result.returncode == 0, result.stderr)
        result = run(str(ROOT / "check_site.py"), "--dir", str(real_out))
        expect("real data: crawl is clean", result.returncode == 0, result.stdout[-800:])
    print(f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
