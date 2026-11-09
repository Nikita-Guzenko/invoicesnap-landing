#!/usr/bin/env python3
"""Go-live gate, browser part (blueprint gates 2 and 3; NIK-547, 8 Nov 2026).

Usage: python3 gate_browser.py --base http://127.0.0.1:8000 /invoice-template/plumbing/ [more paths] [--shots DIR]
Exit 0 = every page passed. Needs: python playwright with Chromium, pdftotext, openpyxl.

For each page, in a real Chromium: clear the form, type marker values, add one line, download PDF, Word, Excel and the
blank PDF, then open the real files:
- pdftotext must find customer, number and total, and no letter of the page's sample text may be lost (gate 3);
- the Word file must parse and hold the same values (no Microsoft Word on this machine);
- on one page of each document kind, LibreOffice opens the Word and Excel files, if it is on the machine: see soffice();
- openpyxl must open the Excel file, and its cached total must equal the page total;
- no network request may hold a typed marker: this proves the "stays in your browser" line on the page;
- the live paper must match the server paper on load, and the phone layout must not scroll sideways.
A tool that cannot run is a failure, not a pass.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
CUSTOMER, NUMBER = "Gate Customer Mx7731", "GT-77313"
OFFICE_PATHS: set[str] = set()  # Filled in run(): one page per document kind opens in LibreOffice. Each open takes about 10 s.


def pdf_text(path: Path) -> str:
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, timeout=60, check=True).stdout


def docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        if z.testzip():
            raise ValueError("bad zip")
        root = ET.fromstring(z.read("word/document.xml"))
        ET.fromstring(z.read("[Content_Types].xml"))
    return " ".join(t.text or "" for t in root.iter(f"{W}t"))


def xlsx_values(path: Path) -> list:
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    return [c for row in wb.active.iter_rows(values_only=True) for c in row if c not in (None, "")]


def soffice() -> str | None:
    """A real office program, to prove that the files open. Not installed by a package here: AppImage extracted to ~/.cache/lo (9 Nov 2026)."""
    local = Path.home() / ".cache/lo/squashfs-root/opt/libreoffice25.8/program/soffice"
    return shutil.which("soffice") or shutil.which("libreoffice") or (str(local) if local.exists() else None)


def strip_cache(path: Path, out: Path) -> Path:
    """The same xlsx without cached formula values. LibreOffice prints a cached value as it is, so a check on the cache could not see a
    wrong formula (review, 9 Nov 2026). Without the cache it must calculate."""
    target = out / ("nocache-" + path.name)
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(target, "w") as dst:
        for name in src.namelist():
            data = src.read(name)
            if name == "xl/worksheets/sheet1.xml":
                data = re.sub(rb"</f><v>[^<]*</v>", b"</f>", data)
            dst.writestr(name, data)
    return target


def fixed_case(page, cfg: dict, base_problems: list[str], out: Path, office: bool) -> None:
    """Known input, expected values worked out here and not taken from the page: $50 labor + $102 parts, 8.25% tax. 102 x 8.25% = 8.415."""
    if not page.locator('[data-f="rate"]').count():
        return  # A law record with tax modes has its own rates.
    page.click("#clear")
    page.fill('[data-f="custName"]', "Fixed Case")
    rows = page.locator("#items .row")
    split = cfg["split"]
    first = rows.nth(0)
    first.locator('[data-i="d"]').fill("Fixed part")
    first.locator('[data-i="q"]').fill("1")
    first.locator('[data-i="r"]').fill("102")
    if split:
        first.locator('[data-i="k"]').select_option("M")
        second = rows.nth(1)
        second.locator('[data-i="d"]').fill("Fixed labor")
        second.locator('[data-i="q"]').fill("1")
        second.locator('[data-i="r"]').fill("$50.00")
        second.locator('[data-i="k"]').select_option("L")
        page.locator('[data-f="taxOn"][value="parts"]').check()
    page.fill('[data-f="rate"]', "8.25%")
    net = 152 if split else 102
    want = {"tax": "8.42", "gross": f"{net + 8.42:,.2f}"}
    if cfg["kind"] == "estimate":
        page.fill('[data-f="depositPct"]', "10")
        want["deposit"] = {152: "16.04", 102: "11.04"}[net]
    else:
        page.fill('[data-f="paid"]', "10")
        want["balance"] = f"{net + 8.42 - 10:,.2f}"
    with page.expect_download(timeout=20000) as info:
        page.click("#dl-pdf")
    pdf = out / "fixed.pdf"
    info.value.save_as(pdf)
    text = pdf_text(pdf)
    for name, value in want.items():
        if value not in text:
            base_problems.append(f"gate 2: fixed case: PDF lacks {name} {value}")
    with page.expect_download(timeout=20000) as info:
        page.click("#dl-xlsx")
    book = out / "fixed.xlsx"
    info.value.save_as(book)
    if office:
        opened = office_text(strip_cache(book, out), out)
        for name, value in want.items():
            if value not in opened:
                base_problems.append(f"gate 2: fixed case: Excel formulas give no {name} {value}: {squash(opened)[-200:]}")
    # Two lines of 3 x 0.335: each rounds to 1.01, so the total is 2.02. Without ROUND per line the sum is 2.01, while the PDF says 2.02.
    if office:
        page.fill('[data-f="rate"]', "0")
        for index in (2, 3):
            row = rows.nth(index)
            row.locator('[data-i="d"]').fill("Fraction line")
            row.locator('[data-i="q"]').fill("3")
            row.locator('[data-i="r"]').fill("0.335")
        with page.expect_download(timeout=20000) as info:
            page.click("#dl-xlsx")
        info.value.save_as(out / "round.xlsx")
        total = f"{net + 2.02:,.2f}"
        if total not in office_text(strip_cache(out / "round.xlsx", out), out):
            base_problems.append(f"gate 2: fixed case: Excel line rounding gives no total {total}")
    # A long note must reach the PDF in full, over more pages. The end word proves that nothing fell off the page.
    if page.locator('[data-f="terms"]').count():
        page.fill('[data-f="terms"]', " ".join(f"w{i}" for i in range(2500)) + " LongNoteEnd")
        with page.expect_download(timeout=20000) as info:
            page.click("#dl-pdf")
        info.value.save_as(out / "long.pdf")
        if "LongNoteEnd" not in pdf_text(out / "long.pdf"):
            base_problems.append("gate 2: a long note did not reach the PDF in full")


def office_text(path: Path, out: Path) -> str:
    """Open the file in LibreOffice, save it as PDF, return the text."""
    exe = soffice()
    subprocess.run([exe, "--headless", f"-env:UserInstallation=file://{out}/lo-profile", "--convert-to", "pdf", "--outdir", str(out / "lo"), str(path)],
                   capture_output=True, timeout=120, check=True)
    return pdf_text(out / "lo" / (path.stem + ".pdf"))


def squash(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace(" ", " ").replace(" ", " ")).strip()


def check_page(ctx, base: str, path: str, out: Path, shots: Path | None) -> list[str]:
    problems: list[str] = []
    page = ctx.new_page()
    requests: list[str] = []
    page.on("request", lambda r: requests.append(r.url + " " + (r.post_data or "")))
    errors: list[str] = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    try:
        page.goto(base + path, wait_until="domcontentloaded", timeout=60000)
        server_paper = squash(page.evaluate("document.getElementById('paper').textContent"))
        page.wait_for_function("window.__invoice!==undefined", timeout=20000)
    except Exception as err:  # A page that cannot load must fail the gate, whatever the reason.
        page.close()
        return [f"gate 2: page did not load: {str(err)[:200]}"]
    live_paper = squash(page.evaluate("document.getElementById('paper').textContent"))
    if server_paper and server_paper != live_paper:
        # The server text comes from the HTML before the script ran only if the script was slow; compare with a fresh fetch.
        raw = page.evaluate("fetch(location.href).then(r=>r.text())")
        doc = page.evaluate("t=>{const d=new DOMParser().parseFromString(t,'text/html');return d.getElementById('paper').textContent}", raw)
        if squash(doc) != live_paper:
            problems.append(f"server paper differs from live paper: {squash(doc)[:160]!r} / {live_paper[:160]!r}")
    cfg = json.loads(page.evaluate("document.getElementById('tool-cfg').textContent"))
    sample_text = " ".join(str(v) for k, v in cfg["sample"].items() if isinstance(v, str) and k not in ("date", "svc", "start"))
    sample_text += " " + " ".join(str(i["d"]) for i in cfg["sample"]["items"])
    with page.expect_download(timeout=20000) as sample_dl:
        page.click("#dl-pdf")
    sample_pdf = out / "sample.pdf"
    sample_dl.value.save_as(sample_pdf)
    got = pdf_text(sample_pdf)
    lost = sorted({c for c in sample_text if c.isalpha() and c not in got})
    if lost:
        problems.append(f"gate 3: PDF lost letters of the locale: {' '.join(lost)[:80]}")
    for word in cfg["sample"]["items"][0]["d"].split()[:2]:
        if word not in got:
            problems.append(f"gate 3: PDF lost the word {word!r}")
    page.click("#clear")
    page.fill('[data-f="bizName"]', "Gate Business")
    page.fill('[data-f="custName"]', CUSTOMER)
    page.fill('[data-f="no"]', NUMBER)
    if cfg["kind"] == "estimate":
        page.fill('[data-f="scope"]', "Gate scope text")
        page.fill('[data-f="depositPct"]', "10")
    first = page.locator("#items .row").first
    first.locator('[data-i="d"]').fill("Gate work line")
    first.locator('[data-i="q"]').fill("2")
    first.locator('[data-i="u"]').fill("each")
    first.locator('[data-i="r"]').fill("123.45")
    add = page.locator("button[data-add]")
    added = None
    if add.count():
        added = json.loads(add.first.get_attribute("data-add"))
        add.first.click()
        paper = squash(page.locator("#paper").inner_text())
        if added[0] not in paper:
            problems.append(f"Add button did not put {added[0]!r} on the paper")
    state = page.evaluate("window.__invoice.totals()")
    total = state["gross"]
    shown = squash(page.locator("[data-total]").first.text_content() or "")
    total_text = f"{total:,.2f}"
    if total_text not in shown.replace(" ", " ") and f"{total:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") not in shown:
        problems.append(f"gate 2: the page shows total {shown}, expected {total_text}")
    files = {}
    for kind, button in (("pdf", "#dl-pdf"), ("docx", "#dl-docx"), ("xlsx", "#dl-xlsx"), ("blank", "#dl-blank")):
        with page.expect_download(timeout=20000) as info:
            page.click(button)
        files[kind] = out / f"{kind}-{info.value.suggested_filename}"
        info.value.save_as(files[kind])
    alt = f"{total:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    text = pdf_text(files["pdf"])
    for needle in (CUSTOMER, NUMBER):
        if needle not in text:
            problems.append(f"gate 2: PDF lacks {needle!r}")
    if total_text not in text and alt not in text:
        problems.append(f"gate 2: PDF lacks the total {total_text}")
    try:
        word = docx_text(files["docx"])
        for needle in (CUSTOMER, NUMBER):
            if needle not in word:
                problems.append(f"gate 2: Word file lacks {needle!r}")
        if total_text not in word and alt not in word:
            problems.append(f"gate 2: Word file lacks the total {total_text}")
    except Exception as err:
        problems.append(f"gate 2: Word file does not open: {err}")
    try:
        values = xlsx_values(files["xlsx"])
        if CUSTOMER not in values or NUMBER not in values:
            problems.append("gate 2: Excel file lacks customer or number")
        if not any(isinstance(v, (int, float)) and abs(v - total) < 0.005 for v in values):
            problems.append(f"gate 2: Excel file lacks the total {total}")
    except Exception as err:
        problems.append(f"gate 2: Excel file does not open: {err}")
    if soffice() and path in OFFICE_PATHS:
        for kind in ("docx", "xlsx"):
            try:
                opened = office_text(files[kind], out)
                for needle in (CUSTOMER, NUMBER):
                    if needle not in opened:
                        problems.append(f"gate 2: {kind} opened in LibreOffice, but lacks {needle!r}")
                if total_text not in opened and alt not in opened:
                    problems.append(f"gate 2: {kind} opened in LibreOffice, but lacks the total {total_text}")
            except Exception as err:
                problems.append(f"gate 2: LibreOffice could not open the {kind} file: {str(err)[:160]}")
    blank = pdf_text(files["blank"])
    if CUSTOMER in blank:
        problems.append("gate 2: the blank PDF holds typed data")
    labels = cfg["law"]["ui"]
    if labels["est_no"] not in blank and labels["inv_no"] not in blank:
        problems.append("gate 2: the blank PDF has no number label, so it cannot be filled in")
    if "0.00" in blank or "0,00" in blank:
        problems.append("gate 2: the blank PDF prints zero amounts")
    office = bool(soffice()) and path in OFFICE_PATHS
    if path in OFFICE_PATHS and not soffice():
        problems.append("gate 2: could not look, no LibreOffice on this machine")
    try:
        fixed_case(page, cfg, problems, out, office)
    except Exception as err:
        problems.append(f"gate 2: fixed case did not run: {str(err)[:200]}")
    markers = (CUSTOMER, NUMBER, "Gate Business", "Gate work line", "Gate scope text", "Fixed Case", "LongNoteEnd")
    leaks = [r[:120] for r in requests if any(m in r or m.replace(" ", "+") in r or m.replace(" ", "%20") in r for m in markers)]
    if leaks:
        problems.append(f"privacy: a request holds a typed value: {leaks[0]}")
    if errors:
        problems.append(f"JavaScript error: {errors[0][:160]}")
    page.close()
    return problems


def overflow(browser, base: str, path: str, shots: Path | None) -> list[str]:
    problems = []
    for name, width, height, mobile in (("phone", 390, 844, True), ("desktop", 1366, 900, False)):
        ctx = browser.new_context(viewport={"width": width, "height": height}, is_mobile=mobile, has_touch=mobile, device_scale_factor=2 if mobile else 1)
        page = ctx.new_page()
        page.goto(base + path, wait_until="load", timeout=60000)
        wide = page.evaluate("document.documentElement.scrollWidth-window.innerWidth")
        if wide > 0:
            problems.append(f"layout: {name} page scrolls sideways by {wide} px")
        small = page.evaluate("[...document.querySelectorAll('main button,main a.btn')].filter(b=>b.offsetParent&&b.getBoundingClientRect().height<24).length")
        if small:
            problems.append(f"layout: {name}: {small} controls are under 24 px")
        if shots:
            shots.mkdir(parents=True, exist_ok=True)
            slug = path.strip("/").replace("/", "-") or "home"
            page.screenshot(path=str(shots / f"{slug}-{name}-fold.png"))
            page.screenshot(path=str(shots / f"{slug}-{name}-full.png"), full_page=True)
        ctx.close()
    return problems


def run(base: str, paths: list[str], shots: Path | None = None) -> dict[str, list[str]]:
    """Path -> problems. Missing tools fail every page."""
    missing = [t for t in ("pdftotext",) if not shutil.which(t)]
    try:
        from playwright.sync_api import sync_playwright
        import openpyxl  # noqa: F401
    except ImportError as err:
        missing.append(str(err))
    if missing:
        return {p: [f"gate 2: could not look, missing {missing}"] for p in paths}
    results: dict[str, list[str]] = {}
    OFFICE_PATHS.clear()
    OFFICE_PATHS.update(p for p in paths if p in ("/invoice-template/plumbing/", "/estimate-template/roofing/", "/invoice-template/consulting/"))
    work = Path(tempfile.mkdtemp(dir=Path.home() / ".cache", prefix="invoicesnap-gate-"))  # /tmp is RAM on this machine.
    try:
        with sync_playwright() as pw:
            # channel="chrome": the machine Chrome. The pip Playwright can ask for a newer Chromium than is on disk (seen 8 Nov 2026:
            # wants build 1223, disk has 1200), and that must not stop a release.
            browser = pw.chromium.launch(headless=True, channel="chrome")
            ctx = browser.new_context(accept_downloads=True, viewport={"width": 1280, "height": 900})
            for path in paths:
                folder = work / path.strip("/").replace("/", "-")
                folder.mkdir(parents=True, exist_ok=True)
                try:
                    results[path] = check_page(ctx, base, path, folder, shots) + overflow(browser, base, path, shots)
                except Exception as err:  # One broken page must not hide the others.
                    results[path] = [f"gate 2: browser run failed: {str(err)[:300]}"]
            browser.close()
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", required=True)
    parser.add_argument("--shots", type=Path)
    parser.add_argument("paths", nargs="+")
    args = parser.parse_args()
    results = run(args.base.rstrip("/"), args.paths, args.shots)
    bad = 0
    for path, problems in results.items():
        for line in problems:
            print(f"FAIL {path}: {line}")
            bad += 1
    print(f"browser gate: {len(results)} pages, {bad} problem(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
