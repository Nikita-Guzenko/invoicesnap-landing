#!/usr/bin/env python3
"""Open every source in data/sources.json in a real browser and look for each number that the pages quote from it.

Usage: python3 verify_sources.py [--only ID,ID] [--pages DIR]   (needs Google Chrome; 2-5 minutes)

Writes data/source-check.json: {source id: {"status", "checked", "missing"}}. Gate 4 reads that file.
status "ok" = page opened and every quoted number is on it. "missing" = page opened, a number is not there.
"unknown" = page did not open (403, timeout). Unknown is not a pass: fix the source or remove the number.
A human still reads rule text: this script checks numbers only.

Why a browser and not curl (8 Nov 2026): the EFA rate table is built by script, and Taskrabbit and Fixr refuse plain
HTTP clients. PDF sources go through pdftotext.
"""
from __future__ import annotations
import argparse, datetime as dt, json, re, subprocess, tempfile, urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


def spellings(n: float) -> list[str]:
    out = {f"{n:,.2f}", f"{n:.2f}"}
    if n == int(n):
        out |= {f"{int(n):,}", str(int(n)), f"{int(n)}.00", f"{int(n):,}.00"}
    else:
        out |= {f"{n:g}", f"{n:.1f}"}
    if n < 1:  # EFA prints 25 cents as 25.0¢, and the pages quote 0.25.
        out |= {f"{n * 100:.1f}¢", f"{n * 100:g}¢"}
    return sorted(out)


# Statutes write some amounts in words (NY GBL 1410: "eight hundred dollars").
WORDS = {800.0: "eight hundred", 120.0: "one hundred twenty", 30.0: "thirty", 20.0: "twenty"}


def on_page(n: float, text: str) -> bool:
    if n in WORDS and WORDS[n] in " ".join(text.lower().split()):  # The page breaks lines inside "one hundred\ntwenty".
        return True
    return any(re.search(r"(?<![\d.,])" + re.escape(s) + r"(?![\d])", text) for s in spellings(n))


def near(low: float, high: float, text: str, limit: int = 300) -> bool:
    def spots(n: float) -> list[int]:
        return [m.start() for s in spellings(n) for m in re.finditer(r"(?<![\d.,])" + re.escape(s) + r"(?![\d])", text)]
    return any(abs(a - b) <= limit for a in spots(low) for b in spots(high))


def range_pairs(pages: list[dict]) -> dict[str, set[tuple[float, float]]]:
    out: dict[str, set[tuple[float, float]]] = {}
    for p in pages:
        for it in (p.get("line_items") or {}).get("items", []):
            if it.get("low") is not None:
                out.setdefault(it["src"], set()).add((float(it["low"]), float(it["high"])))
    return out


def quoted_numbers(pages: list[dict], sources: dict) -> dict[str, set[float]]:
    """Range ends per source, plus dollar amounts in rules and in text beside a link to that source."""
    by_url = {s["url"].rstrip("/"): i for i, s in sources.items()}
    want: dict[str, set[float]] = {i: set() for i in sources}

    def dollars(html: str) -> set[float]:
        # Dollars, percents and day counts. (Review, 9 Nov 2026: "10 percent" and "20 days" were not looked at before.)
        return {float(m.replace(",", "")) for m in re.findall(r"\$(\d[\d,]*(?:\.\d+)?)|", html) if m} | {
            float(m) for m in re.findall(r"\b(\d+(?:\.\d+)?)(?: percent|%|[ -]days?\b)", html)}

    for p in pages:
        for it in p.get("line_items", {}).get("items", []) if p.get("line_items") else []:
            if it.get("low") is not None:
                want[it["src"]] |= {float(it["low"]), float(it["high"])}
        for rule in (p.get("rules") or {}).get("items", []):
            want[rule["src"]] |= dollars(rule["html"])
        blocks = [(p.get("line_items") or {}).get("after_html", ""), (p.get("example") or {}).get("after_html", ""), p.get("body_html", "")]
        for html in blocks:
            # Each sentence with a link: its dollars come from that link. Body text with several links is split on periods.
            for sent in re.split(r"(?<=[.])\s+|</(?:p|tr|li|table|td)>", html or ""):
                for url in re.findall(r'href="(https://[^"]+)"', sent):
                    sid = by_url.get(url.rstrip("/"))
                    if sid:
                        want[sid] |= dollars(sent)
                    elif dollars(sent):
                        want.setdefault("url:" + url, set()).update(dollars(sent))
    return want


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", default="")
    ap.add_argument("--pages", default=str(ROOT / "data/tool-pages"))
    args = ap.parse_args()
    from playwright.sync_api import sync_playwright

    sources = json.loads((ROOT / "data/sources.json").read_text("utf-8"))
    pages = [json.loads(f.read_text("utf-8")) for f in sorted(Path(args.pages).glob("*.json"))]
    want = quoted_numbers(pages, sources)
    pairs = range_pairs(pages)
    outfile = ROOT / "data/source-check.json"
    result = json.loads(outfile.read_text("utf-8")) if outfile.exists() else {}
    only = set(filter(None, args.only.split(",")))
    today = dt.date.today().isoformat()
    bad = 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, channel="chrome")
        page = browser.new_context(user_agent=UA).new_page()
        for sid, nums in sorted(want.items()):
            if only and sid not in only:
                continue
            url = sid[4:] if sid.startswith("url:") else sources[sid]["url"]
            text, why = "", ""
            try:
                if url.lower().endswith(".pdf"):
                    req = urllib.request.Request(url, headers={"User-Agent": UA})
                    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
                        f.write(urllib.request.urlopen(req, timeout=40).read())
                        f.flush()
                        text = subprocess.run(["pdftotext", f.name, "-"], capture_output=True, text=True).stdout
                else:
                    r = page.goto(url, timeout=40000, wait_until="domcontentloaded")
                    page.wait_for_timeout(1500)
                    if r is not None and r.status in (403, 429):  # nysenate.gov refuses bursts, and opens again later (9 Nov 2026).
                        page.wait_for_timeout(45000)
                        r = page.goto(url, timeout=40000, wait_until="domcontentloaded")
                        page.wait_for_timeout(1500)
                    if r is None or r.status != 200:
                        why = f"HTTP {r.status if r else 'none'}"
                    text = page.evaluate("document.body.innerText + ' ' + [...document.querySelectorAll('table')].map(t=>t.textContent).join(' ')")
            except Exception as e:  # a failed look is "unknown", never "ok"
                why = str(e).split("\n")[0][:100]
            if why or len(text) < 200:
                status, missing = "unknown", []
            else:
                missing = [n for n in sorted(nums) if not on_page(n, text)]
                # Both ends of a range must stand close together, or the number may be about something else. 300 characters: measured on
                # all 123 ranges on 9 Nov 2026, the widest true pair is 57 apart.
                for low, high in pairs.get(sid, ()):
                    if not near(low, high, text):
                        missing.append(f"{low}-{high} not close together")
                status = "missing" if missing else "ok"
            result[sid] = {"status": status, "checked": today, "numbers": len(nums), "missing": missing, **({"why": why} if why else {})}
            if status != "ok":
                bad += 1
            print(status, sid, url, why, missing, flush=True)
        browser.close()
    outfile.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", "utf-8")
    print(f"{len(result)} sources in {outfile.name}, {bad} not ok in this run")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
