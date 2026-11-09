#!/usr/bin/env python3
"""Run the checks behind gate 5 and write data/law/<locale>.review.json (NIK-548, 9 Nov 2026). See fact_review.py for the rule.

Steps (cache in ~/.cache/invoicesnap-facts; /tmp is RAM on this machine):
  python3 review_checks.py fetch              open every source in Chrome (PDF: pdftotext), save its text with its sha256
  python3 review_checks.py packets            write one packet per source: the statements that cite it plus the source text
  python3 review_checks.py run gemini   send each packet to that model through pi; a done packet is skipped
  python3 review_checks.py merge              validate all results and write the record; prints what still fails
  python3 review_checks.py packets --retry r2  after a fix: packets with only failed or changed statements (run them, then merge)

Results: results/<checker>/<packet>.json = [{"id", "verdict": "confirmed|refuted|not_found|advice", "quote", "note"}]. Any checker that
writes this format counts (a Claude subagent does). Input to a checker is the packet only: a checker never sees another checker's verdict.

data/law/en-US.fact-sources.json maps a statement with no link in its text to its source id, or to "advice".
Gotchas:
- A "confirmed" verdict whose quote is not in the fetched source text becomes "not_found" at merge: free models can invent quotes.
- Each pi run has no tools, so a checker can read only its packet.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import fact_review as fr  # noqa: E402

CACHE = Path.home() / ".cache/invoicesnap-facts"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
MAX_SOURCE = 60000  # Characters of source text in one packet. Longer sources keep only the windows around words of the claims.
ADVICE = "advice"
# OpenCode free models answered "Free usage limit reached" on 9 Nov 2026, so the second checker runs through pi on a paid plan that Nikita already holds.
# Do not add a pay-per-use provider here: this work is $0 (Nikita, 8 Nov 2026).
# Codex and Kimi logins through pi had expired that day. The other checker is a Claude subagent that writes results/claude/.
MODELS = {"gemini": ("gemini-sub", "gemini-3.8-flash-high")}
WORKERS = 4


def load(law_id: str = "en-US"):
    data = ROOT / "data"
    sources = json.loads((data / "sources.json").read_text("utf-8"))
    law = json.loads((data / "law" / f"{law_id}.json").read_text("utf-8"))
    mapping_file = data / "law" / f"{law_id}.fact-sources.json"
    mapping = json.loads(mapping_file.read_text("utf-8")) if mapping_file.exists() else {}
    pages = [json.loads(f.read_text("utf-8")) for f in sorted((data / "tool-pages").glob("*.json"))]
    seen: dict[str, dict] = {}
    for page in pages:
        for st in fr.page_statements(page, sources):
            seen.setdefault(st["id"], {**st, "pages": []})["pages"].append(f"{page['id']}#{st['place']}")
    for st in fr.law_statements(law):
        seen[st["id"]] = {**st, "pages": [f"data/law/{law_id}.json#{st['place']}"]}
    for st in seen.values():
        mapped = mapping.get(st["id"])
        if mapped:  # "a+b" means the statement draws on two sources.
            st["source_id"], *more = mapped.split("+")
            st["extra_source_ids"] = more
    return sources, seen, mapping


def fetch(only: set[str]) -> int:
    from playwright.sync_api import sync_playwright
    sources, _, _ = load()
    out = CACHE / "src"
    out.mkdir(parents=True, exist_ok=True)
    bad = 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, channel="chrome")
        page = browser.new_context(user_agent=UA).new_page()
        for key, source in sorted(sources.items()):
            if only and key not in only:
                continue
            target = out / f"{key}.txt"
            if target.exists() and not only:
                continue
            url, text, why = source["url"], "", ""
            try:
                if url.lower().split("?")[0].endswith(".pdf"):
                    req = urllib.request.Request(url, headers={"User-Agent": UA})
                    with tempfile.NamedTemporaryFile(suffix=".pdf", dir=CACHE) as f:
                        f.write(urllib.request.urlopen(req, timeout=60).read())
                        f.flush()
                        text = subprocess.run(["pdftotext", "-layout", f.name, "-"], capture_output=True, text=True).stdout
                else:
                    r = page.goto(url, timeout=45000, wait_until="domcontentloaded")
                    page.wait_for_timeout(1500)
                    if r is not None and r.status in (403, 429):  # nysenate.gov refuses bursts and opens again later.
                        page.wait_for_timeout(45000)
                        r = page.goto(url, timeout=45000, wait_until="domcontentloaded")
                        page.wait_for_timeout(1500)
                    if r is None or r.status != 200:
                        why = f"HTTP {r.status if r else 'none'}"
                    # textContent too: Kirkland keeps the permit types in closed accordion steps, which innerText leaves out (9 Nov 2026).
                    text = page.evaluate("(() => { const c = document.body.cloneNode(true); c.querySelectorAll('script,style,noscript').forEach(n => n.remove()); "
                                         "return document.body.innerText + ' ' + [...document.querySelectorAll('table')].map(t=>t.textContent).join(' ') + ' ' + c.textContent.replace(/\\s+/g, ' '); })()")
            except Exception as e:  # A failed look stays a failed look: no text file, so no statement can pass on this source.
                why = str(e).split("\n")[0][:120]
            if why or len(text) < 200:
                print(f"FAILED {key} {url}: {why or 'empty'}", flush=True)
                bad += 1
                target.unlink(missing_ok=True)
                continue
            target.write_text(text, "utf-8")
            (out / f"{key}.meta.json").write_text(json.dumps({"url": url, "fetched": dt.date.today().isoformat(), "sha256": hashlib.sha256(text.encode()).hexdigest(), "chars": len(text)}), "utf-8")
            print(f"ok {key} {len(text)}", flush=True)
        browser.close()
    return 1 if bad else 0


def norm_q(s: str) -> str:
    return re.sub(r"[^a-z0-9$%]+", " ", s.casefold()).strip()


def trim_source(text: str, claims: list[str]) -> str:
    if len(text) <= MAX_SOURCE:
        return text
    # Only long PDFs (EY tracker, statutes) reach this. Windows around numbers and rare words of the claims.
    words = {w for c in claims for w in re.findall(r"[A-Za-z$0-9][\w,.$-]{4,}", c)}
    keep: list[tuple[int, int]] = []
    low = text.casefold()
    for w in words:
        for m in list(re.finditer(re.escape(w.casefold()), low))[:8]:
            keep.append((max(0, m.start() - 500), m.end() + 500))
    keep.sort()
    merged: list[list[int]] = []
    for a, b in keep:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(b, merged[-1][1])
        else:
            merged.append([a, b])
    return "\n[...]\n".join(text[a:b] for a, b in merged)[:MAX_SOURCE]


PROMPT = """You check facts for a website. Work only from the SOURCE TEXT below. Do not use memory. Do not open any other file or page.
Today is {today}. For each STATEMENT decide:
- "confirmed": the source text states this, and every number, state and condition in the statement matches. A statement may have plain instructions ("Check your state") beside the fact: judge the fact only.
- "refuted": the source text says something different. Say what it says.
- "not_found": the source text does not cover this, or covers only part of it.
"quote" must be a short exact copy (under 200 characters) from the SOURCE TEXT that decides the verdict. For a range, the quote holds both numbers. Empty quote when not_found.
Be strict: an extra condition in the statement that the source does not hold is "not_found".

Answer with only a JSON array, no other text: [{{"id": "...", "verdict": "confirmed", "quote": "...", "note": "max 20 words"}}]

SOURCE: {name} - {url}
STATEMENTS:
{statements}

SOURCE TEXT:
{text}
"""

ADVICE_PROMPT = """You sort sentences from a website about invoices. For each STATEMENT decide:
- "advice": it only gives instructions, describes the form or page, or explains why a field is useful. It states no law, tax rule, license rule, money amount or rate that a reader could rely on as a fact about the world.
- "fact": it states any law, tax rule, license rule, deadline, money amount, rate, or says what a state, the IRS or another authority does or requires.
A statement with both parts is "fact". Be strict: when not sure, answer "fact".
A line marked [form label] names a field. A line marked [form sample] is invented demo text typed into the form: its names, dates and amounts are fiction. Answer "advice" for those two unless the line says what a law, a state or an authority requires.

Answer with only a JSON array, no other text: [{{"id": "...", "verdict": "advice", "quote": "", "note": "max 20 words"}}]

STATEMENTS:
{statements}
"""


def form_mark(st: dict) -> str:
    place = st["pages"][0]
    return " [form label]" if "#form.labels." in place else " [form sample]" if "#form.sample." in place else ""


def packets(retry: str = "") -> int:
    sources, seen, _ = load()
    out = CACHE / "packets"
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*"):
        old.unlink()
    suffix = f"~{retry}" if retry else ""
    groups: dict[str, list[dict]] = {}
    orphans = []
    # A rerun packet (name~rN) holds only what still fails or changed: earlier verdicts of the other statements stay valid.
    record_file = ROOT / "data/law/en-US.review.json"
    fails_file = CACHE / "fails.json"
    todo = None
    if retry:
        record = json.loads(record_file.read_text("utf-8"))
        failed = {row["id"] for row in json.loads(fails_file.read_text("utf-8"))}
        # "claim differs" catches a changed column head, which the id does not hold.
        todo = {i for i in seen if i in failed or i not in record["statements"] or record["statements"][i]["claim"] != seen[i]["claim"]}
    for st in seen.values():
        if todo is not None and st["id"] not in todo:
            continue
        url_source = next((k for k, s in sources.items() if st.get("source_url") and s["url"] == st["source_url"]), None)
        key = "+".join(filter(None, [st.get("source_id") or url_source, *st.get("extra_source_ids", [])]))
        if not key:
            orphans.append(st)
            continue
        groups.setdefault(key, []).append(st)
    for st in orphans:
        print(f"NO SOURCE {st['id']} {st['pages'][0]}: {st['claim'][:100]}")
    today = dt.date.today().isoformat()
    n = 0
    for key, sts in sorted(groups.items()):
        lines = "\n".join(f"- {s['id']}: {s['claim']}" for s in sts)
        if key == ADVICE:
            size = 25
            for i in range(0, len(sts), size):
                part = sts[i:i + size]
                (out / f"advice-{i // size}{suffix}.txt").write_text(ADVICE_PROMPT.format(statements="\n".join(f"- {s['id']}{form_mark(s)}: {s['claim']}" for s in part)), "utf-8")
                n += 1
            continue
        parts = key.split("+")
        missing = [k for k in parts if not (CACHE / "src" / f"{k}.txt").exists()]
        if missing:
            print(f"NO SOURCE TEXT {missing}: {len(sts)} statements cannot pass")
            continue
        text = "\n\n=== NEXT SOURCE ===\n\n".join(trim_source((CACHE / "src" / f"{k}.txt").read_text("utf-8"), [s["claim"] for s in sts]) for k in parts)
        (out / f"{key}{suffix}.txt").write_text(PROMPT.format(today=today, name=" and ".join(sources[k]["name"] for k in parts), url=" ".join(sources[k]["url"] for k in parts), statements=lines, text=text), "utf-8")
        n += 1
    print(f"{n} packets, {len(seen)} statements, {len(orphans)} with no source")
    return 1 if orphans else 0


def quote_ok(quote: str, source_norm: str, claim: str = "", kind: str = "") -> bool:
    """A confirmed verdict stands only on a real quote; a range quote must hold both numbers. 87.5 cents is written 87.5¢ in the source, so ends under $1 skip this."""
    q = norm_q(quote)
    if not q or q not in source_norm:
        return False
    if kind == "range":
        low_high = re.match(r".*?: \$([\d.]+)-\$([\d.]+)", claim)
        if low_high and float(low_high.group(1)) >= 1:
            # As numbers, not text: the page says $1.5 where a source says $1.50.
            found = {float(n) for n in re.findall(r"\d+(?:\.\d+)?", quote.replace(",", ""))}
            return all(float(n) in found for n in low_high.groups())
    return True


def parse_json(text: str) -> list[dict] | None:
    match = re.search(r"\[\s*\{.*\}\s*\]", text, re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def pi_once(provider: str, model: str, packet: Path, out_file: Path) -> list[dict] | None:
    """One pi run. pi does not exit after it answers (refs/pi-cli.md), so stop it as soon as the output parses, and after 6 minutes at most."""
    env = {**os.environ, "PATH": f"{os.environ['HOME']}/.npm-global/bin:{os.environ['PATH']}"}
    with out_file.open("w") as sink:
        proc = subprocess.Popen(["pi", "--provider", provider, "--model", model, "--no-tools", "--no-session", "-p", f"@{packet}"],
                                stdin=subprocess.DEVNULL, stdout=sink, stderr=subprocess.DEVNULL, env=env, cwd=CACHE)
        try:
            for _ in range(180):
                time.sleep(2)
                parsed = parse_json(out_file.read_text("utf-8", "replace"))
                if parsed is not None:
                    return parsed
                if proc.poll() is not None:
                    return parse_json(out_file.read_text("utf-8", "replace"))
            return None
        finally:
            proc.kill()
            proc.wait()


def run_model(checker: str, only: set[str]) -> int:
    provider, model = MODELS[checker]
    results = CACHE / "results" / checker
    results.mkdir(parents=True, exist_ok=True)
    (results / "CHECKER").write_text(f"{provider}/{model}\n")
    work = CACHE / "pi-work"
    work.mkdir(exist_ok=True)

    def one(packet: Path) -> bool:
        target = results / f"{packet.stem}.json"
        if target.exists() or (only and packet.stem not in only):
            return True
        for attempt in range(3):
            parsed = pi_once(provider, model, packet, work / f"{checker}-{packet.stem}.out")
            if parsed is not None:
                target.write_text(json.dumps(parsed, indent=1, ensure_ascii=False), "utf-8")
                print(f"ok {checker} {packet.stem} {len(parsed)}", flush=True)
                return True
            print(f"retry {checker} {packet.stem} attempt {attempt}", flush=True)
            time.sleep(20)
        return False

    with ThreadPoolExecutor(WORKERS) as pool:
        done = list(pool.map(one, sorted((CACHE / "packets").glob("*.txt"))))
    print(f"{checker}: {done.count(False)} packets without a result")
    return 1 if not all(done) else 0


def merge(law_id: str = "en-US", write: bool = True) -> int:
    sources, seen, _ = load(law_id)
    today = dt.date.today().isoformat()
    result_dirs = sorted(p for p in (CACHE / "results").glob("*") if p.is_dir())
    # A checker's name in the record is the model that gave the verdict, read from results/<dir>/CHECKER if present.
    name_of = {d.name: (d / "CHECKER").read_text().strip() if (d / "CHECKER").exists() else d.name for d in result_dirs}
    src_norm: dict[str, str] = {}
    verdicts: dict[str, list[dict]] = {}
    for d in result_dirs:
        for f in sorted(d.glob("*.json"), key=lambda p: (p.stem.partition("~")[2], p.stem)):  # Reruns come last, and replace.
            for row in json.loads(f.read_text("utf-8")):
                if row.get("id") not in seen:
                    continue
                verdict, quote = row.get("verdict"), str(row.get("quote") or "")
                packet = f.stem.partition("~")[0]
                if verdict == "fact":
                    verdict = "says_fact"
                elif verdict == "confirmed":
                    key = "+".join(filter(None, [seen[row["id"]].get("source_id"), *seen[row["id"]].get("extra_source_ids", [])])) or packet
                    if key not in src_norm:
                        files = [CACHE / "src" / f"{k}.txt" for k in key.split("+")]
                        src_norm[key] = norm_q(" ".join(p.read_text("utf-8") for p in files if p.exists()))
                    if not quote_ok(quote, src_norm[key], seen[row["id"]]["claim"], seen[row["id"]]["kind"]):
                        row["note"] = f"quote is not in the source: {quote[:80]!r}"
                        verdict = "not_found"
                # The date of the real run, from the result file: a re-merge must not make an old check look new (PR review, 9 Nov 2026).
                ran = dt.date.fromtimestamp(f.stat().st_mtime).isoformat()
                check = {"checker": name_of[d.name], "verdict": verdict, "date": ran, "quote": quote[:240], "note": str(row.get("note") or "")[:200]}
                prev = verdicts.setdefault(row["id"], [])
                prev[:] = [c for c in prev if c["checker"] != check["checker"]]  # A rerun replaces the checker's older verdict.
                prev.append(check)
    record_file = ROOT / "data/law" / f"{law_id}.review.json"
    statements = {}
    fails = []
    for st_id, st in sorted(seen.items()):
        checks = sorted(verdicts.get(st_id, []), key=lambda c: c["checker"])
        key = st.get("source_id")
        is_advice = key == ADVICE
        entry = {"type": "advice" if is_advice else "fact", "kind": st["kind"], "claim": st["claim"], "pages": st["pages"], "checks": checks}
        if not is_advice:
            url = sources[key]["url"] if key in sources else st.get("source_url")
            entry["source_url"] = url
            if st.get("extra_source_ids"):
                entry["also_sources"] = [sources[k]["url"] for k in st["extra_source_ids"]]
            # A statement with a link in its text has no mapped id: find the source by its URL, so the gate can read the fetch date.
            key = key or next((k for k, s in sources.items() if s["url"] == url), None)
            meta = CACHE / "src" / f"{key}.meta.json"
            if meta.exists():
                m = json.loads(meta.read_text())
                entry["source_fetched"], entry["source_sha256"] = m["fetched"], m["sha256"]
        statements[st_id] = entry
        wanted = "advice" if is_advice else "confirmed"
        agree = {c["checker"] for c in checks if c["verdict"] == wanted}
        if len(agree) < fr.MIN_CHECKS or any(c["verdict"] != wanted for c in checks):  # The same test as the gate: any dissent fails.
            fails.append((st_id, st, checks))
    record = {"locale": law_id, "method": "Automated. Each checker model read the fetched source text on its own and did not see the other verdicts. No person reviewed these facts.",
              "updated": today, "checkers": sorted(set(name_of.values())), "statements": statements}
    if write:
        record_file.write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n", "utf-8")
    report = CACHE / "fails.json"
    report.write_text(json.dumps([{"id": i, "claim": s["claim"], "source": s.get("source_id"), "pages": s["pages"], "checks": c} for i, s, c in fails], indent=1, ensure_ascii=False), "utf-8")
    print(f"{len(statements)} statements, {len(statements) - len(fails)} pass, {len(fails)} fail; list: {report}")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["fetch", "packets", "run", "merge"])
    ap.add_argument("checker", nargs="?", choices=list(MODELS))
    ap.add_argument("--retry", default="", help="with packets: rerun name, for example r2. Packets hold only failed or changed statements")
    ap.add_argument("--only", default="", help="comma-separated source ids or packet names")
    args = ap.parse_args()
    only = set(filter(None, args.only.split(",")))
    if args.step == "fetch":
        return fetch(only)
    if args.step == "packets":
        return packets(args.retry)
    if args.step == "run":
        return run_model(args.checker, only)
    return merge()


if __name__ == "__main__":
    raise SystemExit(main())
