#!/usr/bin/env python3
"""Gate 5: facts on a page need a dated record of two agreeing automated checks (NIK-548, Nikita's order, 9 Nov 2026).

Why: blueprint gate 5 asked for a named human reviewer. None exists, and pages must never name or imply one. The record
data/law/<locale>.review.json replaces it: per statement the source URL, the date and the verdict of each checker.

What must be in the record is computed from the data, never listed by hand, so new or edited text counts as unchecked:
- law:   each legal field of data/law/<locale>.json (LAW_FIELDS)
- range: each line item with a low and a high
- rule:  each trade rule, always
- prose: each text block of a page that LEGAL_WORDS matches
A statement id is a hash of its text. Change one word and the old checks no longer count: run the checks again.

Types in the record: "fact" = needs a source and 2 "confirmed" verdicts from 2 different models, and no "refuted" verdict.
"advice" = states no rule, number or source (for example "Check your state"): needs 2 "advice" verdicts from 2 models.

Usage: python3 fact_review.py [--law en-US]       print statements that the record does not cover; exit 1 if any
Re-check after a change: python3 review_checks.py --help
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import re
from pathlib import Path

MAX_AGE_DAYS = 365  # As gate 4: a check older than this is no check.
MIN_CHECKS = 2
LAW_FIELDS = ("tax_name", "tax_rate", "required_fields", "exemption_text", "reverse_charge", "retention", "estimate_word_html", "notes")
# Words that mark a legal, tax, license or money statement. Wide on purpose (tested on all 23 pages, 9 Nov 2026): a false match only
# adds one "advice" row; a miss lets a legal claim go out unchecked.
LEGAL_WORDS = re.compile(
    r"\b(IRS|federal|statutes?|laws?|legal(?:ly)?|licen[sc]\w*|permits?|tax(?:es|ed|able|ation)?|W-9|1099\S*|copyright|cooling-off|FTC|EPA|"
    r"court|liens?|bonds?|bonded|certif\w+|requires?|required|must|exempt\w*|withh\w+|statute|act|section|N\.J\.A\.C|U\.S\.C|median|percent|"
    # Added after the PR review, 9 Nov 2026: "The service date starts any warranty" and "The price holds unless..." went past the first list.
    r"warrant\w+|contracts?|deductibles?|deposits?|writing|written|holds?|binds?|insur\w+|"
    r"Texas|California|Florida|New York|New Jersey|Washington|states?)\b|\$\d|\d%|§|\b\d+ days\b", re.I)
SAMPLE_TEXT = ("terms", "scope", "exclusions", "rights", "lateFee")  # Sample fields with sentences. Names and amounts in a sample are fiction.
BLOCK_END = re.compile(r"</(?:p|li|tr|h2|h3|table|ul|ol|div)>")


def text_of(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def sid(prefix: str, text: str) -> str:
    return f"{prefix}-{hashlib.sha256(' '.join(text.split()).casefold().encode()).hexdigest()[:12]}"


def links(fragment: str) -> list[str]:
    return re.findall(r'href="(https://[^"]+)"', fragment or "")


def page_blocks(page: dict) -> list[tuple[str, str]]:
    """(place, html) of every text block that a reader sees as content. The form and the shared app note stay out (gate 9 holds claims)."""
    # Title, description and h1 too: a search result shows them, so a claim there is a claim on the page (PR review, 9 Nov 2026).
    out: list[tuple[str, str]] = [("title", page.get("title", "")), ("description", page.get("description", "")), ("h1", page.get("h1", "")), ("lede", page.get("lede_html", ""))]
    ms = page.get("must_show") or {}
    if ms:
        out.append(("must_show.intro", ms.get("intro_html", "")))
        out += [(f"must_show[{i}]", f"{a}: {b}") for i, (a, b) in enumerate(ms.get("rows", []))]
    for name in ("example", "line_items"):
        block = page.get(name) or {}
        out += [(f"{name}.{k}", block[k]) for k in ("intro_html", "after_html") if block.get(k)]
    form = page.get("form") or {}
    if form.get("tax_hint"):
        out.append(("form.tax_hint", form["tax_hint"]))
    out += [(f"form.labels.{k}", v) for k, v in (form.get("labels") or {}).items()]
    out += [(f"form.sample.{k}", form["sample"][k]) for k in SAMPLE_TEXT if (form.get("sample") or {}).get(k)]
    for i, part in enumerate(BLOCK_END.split(page.get("body_html", ""))):
        out.append((f"body[{i}]", part))
    out += [(f"faq[{i}]", f"{q['q']} {q['a']}") for i, q in enumerate(page.get("faq", []))]
    out += [(f"rows[{i}]", r.get("html", "")) for i, r in enumerate(page.get("rows", []))]
    return out


def page_statements(page: dict, sources: dict) -> list[dict]:
    """Everything on this page that the record must cover."""
    out: list[dict] = []
    by_url = {s["url"].rstrip("/"): k for k, s in sources.items()}
    for it in (page.get("line_items") or {}).get("items", []):
        if it.get("low") is not None:
            unit = f" per {it['range_unit']}" if it.get("range_unit") else ""
            claim = f"{it['name']}: ${it['low']}-${it['high']}{'+' if it.get('plus') else ''}{unit}"
            # The column head is part of what the page says: "Employee wage range" turns "PR consulting" into a wage, not a consulting fee (9 Nov 2026).
            label = (page.get("line_items") or {}).get("range_label", "Typical range (US)")
            out.append({"id": sid("range", f"{claim}|{it.get('src')}"), "kind": "range", "claim": f"{claim} (column: {label})", "source_id": it.get("src"), "place": "line_items"})
    for i, rule in enumerate((page.get("rules") or {}).get("items", [])):
        claim = f"{rule['title']}. {text_of(rule['html'])}"
        also = [by_url[u.rstrip("/")] for u in links(rule["html"]) if u.rstrip("/") in by_url and by_url[u.rstrip("/")] != rule.get("src")]
        out.append({"id": sid("rule", f"{claim}|{rule.get('src')}"), "kind": "rule", "claim": claim, "source_id": rule.get("src"), "extra_source_ids": also, "place": f"rules[{i}]"})
    for place, fragment in page_blocks(page):
        claim = text_of(fragment)
        if claim and LEGAL_WORDS.search(claim):
            urls = [u for u in links(fragment) if u.rstrip("/") in by_url]
            out.append({"id": sid("prose", claim), "kind": "prose", "claim": claim, "source_id": by_url[urls[0].rstrip("/")] if urls else None,
                        "extra_source_ids": [by_url[u.rstrip("/")] for u in urls[1:]], "place": place})
    return out


def law_statements(law: dict) -> list[dict]:
    out = []
    for field in LAW_FIELDS:
        value = law.get(field)
        if value:
            claim = text_of(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))
            out.append({"id": sid("law", f"{field}|{claim}"), "kind": "law", "claim": f"{field}: {claim}", "source_id": None, "place": field})
    status = (law.get("e_invoice") or {}).get("status")
    if status:
        out.append({"id": sid("law", f"e_invoice|{status}"), "kind": "law", "claim": f"e_invoice: {status}", "source_id": None, "place": "e_invoice", "source_url": law["e_invoice"].get("source")})
    return out


def record_path(data_dir: Path, law_id: str) -> Path:
    return data_dir / "law" / f"{law_id}.review.json"


def load_record(data_dir: Path, law_id: str) -> dict | None:
    path = record_path(data_dir, law_id)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None  # Missing or broken both mean "no record": gate 5 fails.


def statement_problems(st: dict, entry: dict | None, today: str) -> list[str]:
    """Why this statement does not pass, in words that say the fix."""
    label = f"{st['id']} {st['claim'][:70]!r}"
    if entry is None:
        return [f"gate 5: not checked: {label}. Run: python3 review_checks.py --help"]
    # The text in the record must be the text on the page. The id alone leaves out a range's column head (PR review, 9 Nov 2026).
    if entry.get("claim") is not None and entry["claim"] != st["claim"]:
        return [f"gate 5: not checked: {label}: the text changed after the check. Run: python3 review_checks.py --help"]
    checks = entry.get("checks", [])
    kind = entry.get("type")
    if kind not in ("fact", "advice"):
        return [f"gate 5: {label}: type is {kind!r}, expected fact or advice"]
    if kind == "advice" and st["kind"] in ("rule", "range"):
        return [f"gate 5: {label}: a {st['kind']} is always a fact"]  # Otherwise "advice" is a way around the source.
    wanted = "confirmed" if kind == "fact" else "advice"
    agree = {c.get("checker") for c in checks if c.get("verdict") == wanted and c.get("checker")}
    problems = []
    if len(agree) < MIN_CHECKS:
        problems.append(f"gate 5: {label}: {len(agree)} different checkers agree, need {MIN_CHECKS}")
    if any(c.get("verdict") == "refuted" for c in checks):
        problems.append(f"gate 5: {label}: a checker refuted it: fix or cut the fact")
    elif any(c.get("verdict") != wanted for c in checks):
        problems.append(f"gate 5: {label}: a checker disagrees: fix or cut it")  # Two of three is not agreement.
    if kind == "fact":
        if not str(entry.get("source_url", "")).startswith("https://"):
            problems.append(f"gate 5: {label}: no source URL")
        if any(c.get("verdict") == wanted and not c.get("quote") for c in checks):
            problems.append(f"gate 5: {label}: a check has no quote from the source")
    now = dt.date.fromisoformat(today)

    def age_problem(what: str, value) -> None:
        try:
            age = (now - dt.date.fromisoformat(value or "")).days
        except ValueError:
            problems.append(f"gate 5: {label}: {what} has no date")
            return
        if age > MAX_AGE_DAYS or age < 0:
            problems.append(f"gate 5: {label}: {what} is {age} days old, limit {MAX_AGE_DAYS}")

    for c in checks:
        age_problem("a check", c.get("date"))
    if kind == "fact":
        age_problem("the fetched source", entry.get("source_fetched"))
    return problems


def problems_for(page: dict, law_id: str, law: dict, record: dict | None, sources: dict, today: str) -> list[str]:
    """Gate 5 for one page: the law record facts plus the page's own statements."""
    if record is None:
        return [f"gate 5: law record {law_id} has no check record: data/law/{law_id}.review.json is missing or broken"]
    entries = record.get("statements", {})
    out: list[str] = []
    linked = {s["url"].rstrip("/") for k, s in sources.items() if k in page.get("source_ids", [])}
    # sources_html always lists what a rule or a range cites.
    linked |= {sources[r["src"]]["url"].rstrip("/") for r in (page.get("rules") or {}).get("items", []) if r.get("src") in sources}
    linked |= {sources[i["src"]]["url"].rstrip("/") for i in (page.get("line_items") or {}).get("items", []) if i.get("src") in sources}
    for st in law_statements(law) + page_statements(page, sources):
        entry = entries.get(st["id"])
        out += statement_problems(st, entry, today)
        # Nikita's order, 9 Nov 2026: a page that states a fact links its source.
        if st["kind"] != "law" and entry and entry.get("type") == "fact":
            url = str(entry.get("source_url", "")).rstrip("/")
            if url and url not in linked and url not in {u.rstrip("/") for _, f in page_blocks(page) for u in links(f)}:
                out.append(f"gate 5: {st['id']} {st['claim'][:50]!r}: the page does not link its source {url}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--law", default="en-US")
    ap.add_argument("--data", type=Path, default=Path(__file__).parent / "data")
    args = ap.parse_args()
    law = json.loads((args.data / "law" / f"{args.law}.json").read_text("utf-8"))
    sources = json.loads((args.data / "sources.json").read_text("utf-8"))
    record = load_record(args.data, args.law)
    today = dt.date.today().isoformat()
    bad = 0
    for f in sorted((args.data / "tool-pages").glob("*.json")):
        page = json.loads(f.read_text("utf-8"))
        if page.get("law", "en-US") != args.law:
            continue
        for line in problems_for(page, args.law, law, record, sources, today):
            print(f"{page['id']}: {line}")
            bad += 1
    print(f"{bad} problem(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
