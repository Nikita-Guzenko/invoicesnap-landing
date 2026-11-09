#!/usr/bin/env python3
"""End-to-end test of the go-live gate for the free tool pages (NIK-547, NIK-548; 9 Nov 2026). Real build, real HTTP, real Chrome.

Usage: python3 tests/test_tool_gate.py     (about 3 minutes; needs Google Chrome, pdftotext, openpyxl)

1. A German page on a de-DE law record builds live and passes the browser gate. Proof that a country is data, not code.
2. The same page without a named reviewer is blocked with noindex (gate 5). A waiver stands only with a written decision of Nikita on the default locale.
3. An Arabic page keeps dir="rtl" and fails gate 3, because the PDF font has no Arabic letters.
4. Each data gate can fail, and the failed page leaves the sitemap and gets noindex.
5. Breaking the served tool makes the browser gate fail: wrong PDF, form data on the network, a broken Excel file.
6. check_site.py fails on bytes from the server that the gate forbids.
Fixture text is throwaway; it never goes live.
"""
from __future__ import annotations

import copy
import http.server
import json
import shutil
import subprocess
import sys
import tempfile
import threading
from functools import partial
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import gate_browser  # noqa: E402

FIX = ROOT / "tests/fixtures"
failures: list[str] = []
SOME_PAGE = "plumbing-invoice"
SOME_PATH = "/invoice-template/plumbing/"


def expect(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f": {str(detail)[:500]}" if not ok else ""), flush=True)
    if not ok:
        failures.append(name)


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *args], capture_output=True, text=True, timeout=500)


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def make_data(tmp: Path) -> Path:
    data = tmp / "data"
    shutil.copytree(ROOT / "data", data)
    locales = load(data / "locales.json")
    for tag in ("de", "ar"):
        locales[tag]["ui"] = locales["en"]["ui"]
    save(data / "locales.json", locales)
    en = load(data / "law/en-US.json")
    en["reviewer"] = "Test Reviewer (fixture only)"  # The real record has none, so all real pages are noindex; these tests need live pages.
    save(data / "law/en-US.json", en)
    law = load(FIX / "de-DE.law.json")
    law["ui"] = {**copy.deepcopy(en["ui"]), **law.pop("ui_overrides")}
    save(data / "law/de-DE.json", law)
    ar = {**copy.deepcopy(law), "locale": "ar-SA", "country": "SA", "currency": "SAR", "dir": "rtl", "reviewer": "Test Reviewer (fixture only)"}
    save(data / "law/ar-SA.json", ar)
    sources = load(data / "sources.json")
    sources["fx-de"] = {"name": "Fixture source", "short": "Fixture", "url": "https://www.gesetze-im-internet.de/ustg_1980/__14.html", "checked": "2026-11-09", "used_for": "test"}
    save(data / "sources.json", sources)
    check = load(data / "source-check.json")
    check["fx-de"] = {"status": "ok", "checked": "2026-11-09", "numbers": 0, "missing": []}
    save(data / "source-check.json", check)
    demand = load(data / "demand.json")
    demand["fixture demand row"] = {"volume": None, "source": "fixture"}
    save(data / "demand.json", demand)
    page = load(FIX / "de-plumbing.page.json")
    save(data / "tool-pages/fixture-de.json", page)
    # The freelancer page may be blocked for its own data; these tests are about the fixture pages and SOME_PAGE.
    arabic = copy.deepcopy(page)
    arabic.update(id="fixture-ar", locale="ar", law="ar-SA", slug="fatura/sibaka", title="فاتورة السباكة", h1="نموذج فاتورة السباكة", description="صفحة اختبار لا تنشر.")
    # Own text, or gate 6 would block both fixture pages as near-duplicates of each other.
    def arabize(node, key=""):
        if isinstance(node, dict):
            return {k: arabize(v, k) if k in ("heading", "intro_html", "why_label", "rows", "items", "html", "title", "q", "a", "name", "line_items", "must_show", "rules", "faq", "lede_html") else v for k, v in node.items()}
        if isinstance(node, list):
            return [arabize(v, key) for v in node]
        arabize.n += 1
        return " ".join(f"نص{arabize.n}{i}" for i in range(8)) if isinstance(node, str) else node
    arabize.n = 0
    for part in ("must_show", "line_items", "rules", "faq"):
        arabic[part] = arabize(arabic[part])
    arabic["example"] = {**arabic["example"], "heading": "مثال", "intro_html": "فاتورة ثانية"}
    arabic["form"]["sample"]["custName"] = "شركة السباكة"
    arabic["form"]["sample"]["items"] = [{"d": "عمل السباكة", "q": 2, "u": "ساعة", "r": 90, "k": "L"}]
    arabic["example"]["items"] = [{"d": "صيانة السخان", "q": 1, "u": "وحدة", "r": 150, "k": "L"}]
    save(data / "tool-pages/fixture-ar.json", arabic)
    return data


def build(data: Path, out: Path) -> subprocess.CompletedProcess:
    return run(str(ROOT / "build.py"), "--data", str(data), "--out", str(out), "--today", "2026-11-09")


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def serve(folder: Path):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=str(folder)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def blocked(out: Path, data_edit_path: str) -> tuple[bool, bool]:
    raw = (out / data_edit_path.strip("/") / "index.html").read_text(encoding="utf-8")
    listed = any(data_edit_path in f.read_text(encoding="utf-8") for f in out.glob("sitemap-*.xml"))
    return 'content="noindex"' in raw, listed


def main() -> int:
    with tempfile.TemporaryDirectory(dir=Path.home() / ".cache", prefix="invoicesnap-test-") as t:
        tmp = Path(t)
        data, out = make_data(tmp), tmp / "dist"
        res = build(data, out)
        expect("fixture builds", res.returncode == 0, res.stderr)
        de_path, ar_path = "/de/rechnung-vorlage/sanitaer/", "/ar/fatura/sibaka/"
        noindex, listed = blocked(out, de_path)
        expect("1 German page is live and in the sitemap", not noindex and listed, res.stderr[-600:])
        server, base = serve(out)
        results = gate_browser.run(base, [de_path, ar_path, SOME_PATH])
        expect("1 German page passes the browser gate", results[de_path] == [], results[de_path])
        raw = (out / de_path.strip("/") / "index.html").read_text(encoding="utf-8")
        expect("1 German page carries the record: tax modes and A4", "§ 13b UStG" in raw and "595" in raw and "EUR" in raw, "")
        expect("3 Arabic page fails gate 3", any("gate 3" in p for p in results[ar_path]), results[ar_path])
        raw = (out / ar_path.strip("/") / "index.html").read_text(encoding="utf-8")
        expect('3 Arabic page has dir="rtl"', 'lang="ar" dir="rtl"' in raw, raw[:200])
        expect("control: an English page passes the browser gate", results[SOME_PATH] == [], results[SOME_PATH])
        check = run(str(ROOT / "check_site.py"), "--dir", str(out), "--data", str(data), "--no-browser")
        expect("control: check_site passes without browser", check.returncode == 0, check.stdout[-900:])

        # 2 and 4: each data gate can fail; the failed page gets noindex and leaves the sitemap.
        def bad(name: str, reason: str, file: str, edit, path: str = SOME_PATH) -> None:
            target = data / file
            before = target.read_text(encoding="utf-8")
            value = json.loads(before)
            edit(value)
            save(target, value)
            res = build(data, tmp / "bad")
            target.write_text(before, encoding="utf-8")
            if res.returncode != 0:  # "no build" is the other allowed outcome of a failed gate.
                expect(f"build stops: {name}", reason in res.stderr, res.stderr[-400:])
                return
            noindex, listed = blocked(tmp / "bad", path)
            expect(f"gate fails: {name}", reason in res.stderr and noindex and not listed, f"noindex={noindex} listed={listed}; {res.stderr[-500:]}")

        page = f"tool-pages/{SOME_PAGE}.json"
        items = lambda v: v["line_items"]["items"]  # noqa: E731
        bad("no reviewer on en-US", "gate 5: law record en-US has no named human reviewer", "law/en-US.json", lambda v: v.update(reviewer=None), SOME_PATH)
        bad("no reviewer on a non-default locale", "gate 5: law record de-DE has no named human reviewer", "law/de-DE.json", lambda v: v.update(reviewer=None), de_path)
        bad("waiver on a non-default locale", "gate 5: law record de-DE has no named human reviewer", "law/de-DE.json", lambda v: v.update(reviewer=None, reviewer_waiver={"ticket": "x"}), de_path)
        bad("no law record", "no law record xx-XX", page, lambda v: v.update(law="xx-XX"))
        bad("too few line items", "gate 4: 7 line items", page, lambda v: v["line_items"].update(items=items(v)[:7]))
        bad("too few ranges", "sourced ranges, need 4", page, lambda v: [i.pop("low") for i in items(v) if i.get("low") is not None])
        bad("range source not on file", "is not in data/sources.json", page, lambda v: items(v)[0].update(src="nope"))
        bad("sample outside its range", "is outside its range", page, lambda v: items(v)[0].update(sample=99999))
        bad("too few rules", "trade rules, need 3", page, lambda v: v["rules"].update(items=v["rules"]["items"][:2]))
        bad("source too old", "was checked", "source-check.json", lambda v: v["tr-plumb"].update(checked="2024-01-01"))
        bad("source status unknown", "status is unknown", "source-check.json", lambda v: v["tr-plumb"].update(status="unknown"))
        bad("source number missing", "status is missing", "source-check.json", lambda v: v["tr-plumb"].update(status="missing"))
        bad("no demand", "gate 7: no demand proof", page, lambda v: v.update(demand=[]))
        bad("demand row unknown", "is not in data/demand.json", page, lambda v: v.update(demand=["no such search"]))
        bad("filled job copied from a sibling", "filled job line also on", page, lambda v: v["form"]["sample"]["items"][0].update(d=load(data / "tool-pages/hvac-invoice.json")["form"]["sample"]["items"][0]["d"]))
        bad("no second filled job", "no second filled job", page, lambda v: v.update(example=None) if False else v["example"].update(items=[]))
        bad("forbidden claim", "gate 8: forbidden text", page, lambda v: v.update(lede_html="Trusted by 5,000 contractors."))
        bad("TODO left in text", "gate 8: forbidden text", page, lambda v: v.update(lede_html="TODO verify this."))
        bad("legal-validity claim", "gate 8: forbidden text", page, lambda v: v.update(lede_html="This form is legally valid."))
        sibling = load(data / "tool-pages/hvac-invoice.json")
        bad("near-duplicate of a sibling", "gate 6: overlap with", page, lambda v: v.update(must_show=sibling["must_show"], line_items=sibling["line_items"], rules=sibling["rules"], faq=sibling["faq"], example={**v["example"]}))
        bad("claim no longer at its code line", "no longer holds", "claims.json", lambda v: v["app_note"][0].update(proof="NOT_IN_THE_APP") if "proof" in v["app_note"][0] else None)

        # 5: break the served tool. If the gate stays green here, it measures nothing.
        js = out / "assets/tool.js"
        original = js.read_text(encoding="utf-8")
        for name, reason, change in [
            ("PDF download writes empty text", "gate 2: PDF lacks", lambda s: s.replace("function pdf(d){", "function pdf(d){d=doc(blank());")),
            ("form data goes on the network", "privacy: a request holds", lambda s: s.replace("function render(){", "function render(){try{fetch('/x?c='+encodeURIComponent(S.custName))}catch(e){}")),
            ("Excel formula is wrong under a right cached value", "Excel formulas give no", lambda s: s.replace("'ROUND(B'+n+'*D'+n+',2)'", "'ROUND(B'+n+'*D'+n+'*3,2)'")),
            ("Excel line does not round", "Excel line rounding", lambda s: s.replace("'ROUND(B'+n+'*D'+n+',2)'", "'B'+n+'*D'+n")),
            ("tax rounds down on a half cent", "fixed case: PDF lacks tax", lambda s: s.replace("(n*100).toPrecision(12)", "n*100")),
            ("long text falls off the page", "long note did not reach", lambda s: s.replace("function para(s){wrap(s,8.5,R-M).forEach(function(l){need(11);T(l,M,y,8.5);y-=11});y-=5}", "function para(s){var ls=wrap(s,8.5,R-M).slice(0,50);ls.forEach(function(l){need(11);T(l,M,y,8.5);y-=11});y-=5}")),
            ("blank PDF prints zero amounts", "the blank PDF prints zero", lambda s: s.replace("d=src?blankDoc(src):doc(s)", "d=doc(s)")),
            ("Excel file is broken", "gate 2: Excel file", lambda s: s.replace("function xlsx(d,s){", "function xlsx(d,s){return ['not a zip'];")),
        ]:
            changed = change(original)
            expect(f"test can break: {name}", changed != original, "")
            js.write_text(changed, encoding="utf-8")
            result = gate_browser.run(base, [SOME_PATH])[SOME_PATH]
            expect(f"browser gate fails: {name}", any(reason in p for p in result), result)
        js.write_text(original, encoding="utf-8")
        server.shutdown()

        # What people really type in a number field.
        from playwright.sync_api import sync_playwright
        server, base = serve(out)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, channel="chrome")
            pg = browser.new_page()
            pg.goto(base + SOME_PATH)
            pg.wait_for_function("window.__invoice!==undefined")
            for typed, value in [("1,250.50", 1250.5), ("1,250", 1250), ("$100", 100), ("8.25%", 8.25), ("2 hrs", 2), ("1,5", 1.5), ("1.250,50", 1250.5), ("0.875", 0.875), ("abc", 0), ("", 0), ("1/2", 0), ("1.2.3", 0), ("5-10", 0), ("3 1/2", 0), ("approx. 100", 0), ("2 x 3", 0), ("100 €", 100)]:
                got = pg.evaluate("v=>window.__invoice.parse(v)", typed)
                expect(f"number parse: {typed!r}", got == value, got)
            pg.locator('#items .row [data-i="q"]').first.fill("1/2")
            expect("a number that is not clear gets a warning", "1/2" in pg.inner_text("#missing"), pg.inner_text("#missing"))
            pg.locator('#items .row [data-i="q"]').first.fill("1")
            pg.fill('[data-f="dueDays"]', "99999999999")
            expect("a huge day count does not stop the form", "Download" in pg.inner_text("body") and pg.evaluate("document.getElementById('paper').textContent.length>50"), "")
            pg.fill('[data-f="terms"]', "bad\u000bchar & <x>")
            with pg.expect_download() as d:
                pg.click("#dl-xlsx")
            d.value.save_as(tmp / "ctrl.xlsx")
            expect("a control character does not break Excel", bool(gate_browser.xlsx_values(tmp / "ctrl.xlsx")), "")
            browser.close()
        server.shutdown()

        # 6: bytes on the server.
        file = out / SOME_PATH.strip("/") / "index.html"
        raw_page = file.read_text(encoding="utf-8")
        for name, reason, changed in [
            ("rating in JSON-LD", "gate 8: JSON-LD holds", raw_page.replace('"@type": "WebApplication"', '"aggregateRating": {"@type": "AggregateRating"}, "@type": "WebApplication"', 1)),
            ("a 4.9-star claim", "gate 8: forbidden text", raw_page.replace("<title>", "<title>4.9-star app: ", 1)),
            ("claim in the title", "gate 8: forbidden text", raw_page.replace("<title>", "<title>Rated 4.9 stars: ", 1)),
            ("the #1 claim in the description", "gate 8: forbidden text", raw_page.replace('<meta name="description" content="', '<meta name="description" content="The #1 invoice tool. ', 1)),
            ("claim added on the server", "gate 8: forbidden text", raw_page.replace("</main>", "<p>Rated 4.9 by users</p></main>", 1)),
            ("app note changed on the server", "gate 9: the app note differs", raw_page.replace("19 invoice designs", "190 invoice designs", 1)),
            ("same h1 as a sibling", "gate 1: same h1", raw_page.replace("<h1>Plumbing invoice template</h1>", "<h1>HVAC invoice template</h1>", 1)),
        ]:
            expect(f"test can break: {name}", changed != raw_page, "")
            file.write_text(changed, encoding="utf-8")
            res = run(str(ROOT / "check_site.py"), "--dir", str(out), "--data", str(data), "--no-browser")
            expect(f"check_site fails: {name}", res.returncode == 1 and reason in res.stdout, res.stdout[-500:])
        file.write_text(raw_page, encoding="utf-8")
    print(f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
