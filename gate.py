#!/usr/bin/env python3
"""Go-live gate for the free tool pages, static part (blueprint gates 1, 4 to 9; NIK-548, 8 Nov 2026). Standard library only.

Used by build.py (a page that fails is built with noindex and left out of the sitemap, links and hubs) and by
check_site.py (the same checks on bytes fetched from the served site; any failure exits 1, so publish.py stops).
gate_browser.py holds gates 2 and 3 (real browser, real files).

Numbers marked J in the blueprint are judgment, not Google rules: MIN_ITEMS, MIN_RANGES, MIN_RULES, MAX_AGE_DAYS, MAX_OVERLAP.
Passing is not proof of value (playbook R38): the gate blocks known failures; it does not prove that a page is good.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import re
from html.parser import HTMLParser
from pathlib import Path

import fact_review

MIN_ITEMS, MIN_RANGES, MIN_RULES, MAX_AGE_DAYS = 8, 4, 3, 365
MAX_OVERLAP = 0.30  # 5-word overlap with any sibling, outside data-shared blocks. NIK-548's 0.50 is the weak floor.
SHINGLE = 5
TRADE_TYPES = {"trade_invoice", "trade_estimate", "freelancer_invoice"}
FORBIDDEN_LD = {"AggregateRating", "Review", "Rating", "Person"}  # Person: no human name, also in schema (Nikita, 9 Nov 2026).
# Rule 8 and R25. Word boundaries, so "bestow" or "rated" inside "generated" cannot match. Tested on the real pages.
FORBIDDEN_TEXT = re.compile(
    r"TODO verify|\btrusted by\b|\b\d[\d,]*\+? (?:users|customers|contractors|businesses|downloads)\b|\b(?:star|stars)\b rating|"
    r"\brated \d|\b\d(?:\.\d)?[- ]stars?\b|\bfive stars?\b|\btop[- ]rated\b|\bno\. ?1\b|\b\d[\d,]* reviews\b|\b(?:thousands|millions|hundreds) of (?:users|customers|contractors|businesses)\b|"
    r"\blegally (?:valid|compliant|binding invoice)\b|\bfully compliant\b|(?<!\w)#1\b|\bnumber one\b|\bthe best\b|\bbest[- ](?:in|invoice|estimate|app|free)\b|"
    r"\bworld-class\b|\bunbeatable\b|\bhurry\b|\bact now\b|\blimited time\b|\bguaranteed\b|"
    # Nikita's order, 9 Nov 2026: no page names a human reviewer or says that a person reviewed it. "No person reviewed it" stays allowed.
    r"\breviewers?\b|\breviewed\b|\bapproved by\b|\bchecked by\b|\bverified by\b", re.I)
HONEST_LINE = "No person reviewed this page."  # The one sentence with that word that the gate allows: it is the opposite of a claim.
NOT_ADVICE = "not tax or legal advice"  # Nikita's order, 9 Nov 2026: each page says this plainly. tool_pages.sources_html prints it.


class Parsed(HTMLParser):
    """Text outside data-shared blocks, h1, title, JSON-LD and the visible text."""

    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.shared_at: int | None = None
        self.main = 0
        self.unique: list[str] = []
        self.visible: list[str] = []
        self.h1 = ""
        self.ld: list[str] = []
        self.robots = ""
        self.in_ld = False
        self.app_note: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta" and (a.get("name") or "").lower() == "robots":
            self.robots = (a.get("content") or "").lower()
        if tag == "script" and a.get("type") == "application/ld+json":
            self.in_ld = True
        if tag in self.VOID:
            return
        self.stack.append(tag)
        if "data-shared" in a and self.shared_at is None:
            self.shared_at = len(self.stack)
        if a.get("id") == "app":
            self.app_note_at = len(self.stack)

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_ld = False
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i] == tag:
                del self.stack[i:]
                break
        if self.shared_at is not None and len(self.stack) < self.shared_at:
            self.shared_at = None
        if getattr(self, "app_note_at", None) is not None and len(self.stack) < self.app_note_at:
            self.app_note_at = None

    def handle_data(self, data):
        if self.in_ld:
            self.ld.append(data)
            return
        if not self.stack or self.stack[-1] in ("script", "style", "title", "option"):
            return
        if "main" not in self.stack and "body" not in self.stack:
            return
        self.visible.append(data)
        if "main" in self.stack:
            if self.stack[-1] == "h1":
                self.h1 += data
            if self.shared_at is None:
                self.unique.append(data)
            if getattr(self, "app_note_at", None) is not None and self.stack[-1] == "p":
                self.app_note.append(data)


def parse(raw: str) -> Parsed:
    p = Parsed()
    p.feed(raw)
    p.close()
    return p


def norm(text: str) -> str:
    return " ".join(html.unescape(text).split()).casefold()


def shingles(text: str) -> set[tuple[str, ...]]:
    words = re.findall(r"[\w$%.,/-]+", text.casefold())
    return {tuple(words[i:i + SHINGLE]) for i in range(len(words) - SHINGLE + 1)}


def overlap(a: set, b: set) -> float:
    """Share of the smaller page that also stands in the other. Stricter than Jaccard: a short page cannot hide in a long one."""
    return len(a & b) / min(len(a), len(b)) if a and b else 0.0


def visible_ld_strings(node) -> list[str]:
    """Text values in JSON-LD that a visitor must be able to see (R12). URLs, types and codes stay out."""
    out = []
    if isinstance(node, dict):
        kind = node.get("@type")
        if kind in ("WebApplication", "CollectionPage", "ListItem", "Question") and node.get("name"):
            out.append(node["name"])
        if kind == "Answer" and node.get("text"):
            out.append(node["text"])
        for key, value in node.items():
            if key not in ("@id", "url", "item", "isPartOf", "about"):
                out += visible_ld_strings(value)
    elif isinstance(node, list):
        for value in node:
            out += visible_ld_strings(value)
    return out


def ld_types(node) -> set[str]:
    out: set[str] = set()
    if isinstance(node, dict):
        if isinstance(node.get("@type"), str):
            out.add(node["@type"])
        for value in node.values():
            out |= ld_types(value)
    elif isinstance(node, list):
        for value in node:
            out |= ld_types(value)
    return out


def data_problems(page: dict, sources: dict, demand: dict, laws: dict, default_law: str, today: str) -> list[str]:
    """Gates 4, 5 and 7 from the data files."""
    problems: list[str] = []
    typ = page["type"]
    now = dt.date.fromisoformat(today)

    def check_source(src_id: str, where: str) -> None:
        s = sources.get(src_id)
        if not s:
            problems.append(f"gate 4: {where}: source {src_id} is not in data/sources.json")
            return
        if not s.get("url", "").startswith("https://") or not s.get("name"):
            problems.append(f"gate 4: {where}: source {src_id} needs a name and an https URL")
        try:
            age = (now - dt.date.fromisoformat(s.get("checked", ""))).days
        except ValueError:
            problems.append(f"gate 4: {where}: source {src_id} has no checked date")
            return
        if age > MAX_AGE_DAYS or age < 0:
            problems.append(f"gate 4: {where}: source {src_id} was checked {age} days ago, limit {MAX_AGE_DAYS}")
        # status comes from data/source-check.json (verify_sources.py opened the page). A failed look is not a pass.
        if s.get("status") != "ok":
            problems.append(f"gate 4: {where}: source {src_id} status is {s.get('status', 'never checked')}. Run: python3 verify_sources.py")

    if typ in TRADE_TYPES:
        items = page["line_items"]["items"]
        ranges = [i for i in items if i.get("low") is not None]
        if len(items) < MIN_ITEMS:
            problems.append(f"gate 4: {len(items)} line items, need {MIN_ITEMS}")
        if len(ranges) < MIN_RANGES:
            problems.append(f"gate 4: {len(ranges)} sourced ranges, need {MIN_RANGES}")
        for item in ranges:
            if not item.get("src"):
                problems.append(f"gate 4: range {item['name']!r} has no source")
            else:
                check_source(item["src"], f"range {item['name']!r}")
            if item.get("sample") not in (None, "") and not float(item["low"]) <= float(item["sample"]) <= float(item["high"]) and not item.get("plus"):
                problems.append(f"gate 4: {item['name']!r}: sample price {item['sample']} is outside its range")
        for item in items:
            if not item.get("unit"):
                problems.append(f"gate 4: line item {item['name']!r} has no unit")
        rules = page["rules"]["items"]
        if len(rules) < MIN_RULES:
            problems.append(f"gate 4: {len(rules)} trade rules, need {MIN_RULES}")
        for rule in rules:
            if rule.get("src"):
                check_source(rule["src"], f"rule {rule['title']!r}")
            else:
                problems.append(f"gate 4: rule {rule['title']!r} has no source")
    for src_id in page.get("source_ids", []):
        check_source(src_id, "sources list")
    law_id = page.get("law", default_law)
    law = laws.get(law_id)
    if not law:
        problems.append(f"gate 5: no law record {law_id}")
    else:
        if not law.get("official_urls"):
            problems.append("gate 5: the law record has no official URL")
        # Nikita's order, 9 Nov 2026 (NIK-548): no human reviewer exists, and a page must never name one. The gate passes on a record instead:
        # data/law/<locale>.review.json, each fact with a source and two agreeing checks by different models. It fails on a missing record
        # and on any fact without those checks. "review_record" is loaded by tool_pages.Context. Rule and proof: fact_review.py, tests/test_tool_gate.py.
        problems += fact_review.problems_for(page, law_id, law, law.get("review_record"), sources, today)
    rows = page.get("demand", [])
    if not rows:
        problems.append("gate 7: no demand proof")
    for row in rows:
        if row not in demand:
            problems.append(f"gate 7: {row!r} is not in data/demand.json")
    return problems


def app_note_text(claims: dict, page: dict) -> str:
    """The app note of this page. A claim with only_if_field stands only on a form that has that field: "such as a service date"
    was false on estimate and freelance pages, which have no such field (review, 9 Nov 2026)."""
    sample = (page.get("form") or {}).get("sample", {})
    return " ".join(c["text"] for c in claims.get("app_note", []) if c.get("only_if_field") is None or c["only_if_field"] in sample)


def html_problems(page: dict, raw: str, claims: dict) -> list[str]:
    """Gates 1 (the part a build can see), 8 and 9, on the bytes of the page."""
    problems: list[str] = []
    parsed = parse(raw)
    text = " ".join(" ".join(parsed.visible).split())
    title = re.search(r"<title>(.*?)</title>", raw, re.S)
    described = re.search(r'<meta name="description" content="(.*?)"', raw, re.S)
    # The title and description are what a search result shows, so the same rule covers them.
    for match in FORBIDDEN_TEXT.finditer(" ".join([text.replace(HONEST_LINE, ""), title.group(1) if title else "", described.group(1) if described else ""])):
        problems.append(f"gate 8: forbidden text {match.group(0)!r}")
        break
    visible = norm(text)
    if NOT_ADVICE not in visible:
        problems.append(f"gate 5: the page does not say {NOT_ADVICE!r}")
    for block in parsed.ld:
        try:
            data = json.loads(block)
        except json.JSONDecodeError as err:
            problems.append(f"gate 8: JSON-LD does not parse: {err}")
            continue
        bad = ld_types(data) & FORBIDDEN_LD
        if "reviewedBy" in block:
            bad = bad | {"reviewedBy"}
        if bad:
            problems.append(f"gate 8: JSON-LD holds {sorted(bad)}")
        for value in visible_ld_strings(data):
            if norm(value) not in visible:
                problems.append(f"gate 8: JSON-LD text is not on the page: {value[:70]!r}")
    if not parsed.h1.strip():
        problems.append("gate 1: no h1")
    allowed = norm(app_note_text(claims, page))
    note = norm(" ".join(parsed.app_note))
    if note and note != allowed:
        problems.append("gate 9: the app note differs from data/claims.json")
    repeats = visible.count(norm(page["h1"]))
    if repeats > 4:
        problems.append(f"R23: the exact h1 stands {repeats} times in the text, limit 4")
    return problems


def filled_jobs(page: dict) -> set[str]:
    """Line descriptions of the two filled jobs of a trade page: the form sample and the example."""
    jobs = [i["d"] for i in page["form"]["sample"]["items"]] + [i["d"] for i in (page.get("example") or {}).get("items", [])]
    return {norm(j) for j in jobs}


def filled_job_problems(pages: list[dict]) -> dict[str, list[str]]:
    """Gate 4: "2 filled jobs that no sibling uses". A job line on two pages fails both pages."""
    out: dict[str, list[str]] = {}
    trade = [p for p in pages if p["type"] in TRADE_TYPES]
    for p in trade:
        if not p.get("example") or not p["example"].get("items"):
            out.setdefault(p["id"], []).append("gate 4: no second filled job")
    for i, a in enumerate(trade):
        for b in trade[i + 1:]:
            same = filled_jobs(a) & filled_jobs(b)
            if same:
                line = f"gate 4: filled job line also on {{}}: {sorted(same)[0][:60]!r}"
                out.setdefault(a["id"], []).append(line.format(b["id"]))
                out.setdefault(b["id"], []).append(line.format(a["id"]))
    return out


def overlap_problems(texts: dict[str, str]) -> dict[str, list[str]]:
    """Gate 6. texts: page key -> text outside shared blocks."""
    sets = {k: shingles(v) for k, v in texts.items()}
    out: dict[str, list[str]] = {}
    keys = sorted(sets)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            share = overlap(sets[a], sets[b])
            if share > MAX_OVERLAP:
                out.setdefault(a, []).append(f"gate 6: overlap with {b} is {share:.2f}, limit {MAX_OVERLAP}")
                out.setdefault(b, []).append(f"gate 6: overlap with {a} is {share:.2f}, limit {MAX_OVERLAP}")
    return out


def max_overlap(texts: dict[str, str]) -> tuple[float, str, str]:
    sets = {k: shingles(v) for k, v in texts.items()}
    best = (0.0, "", "")
    keys = sorted(sets)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            share = overlap(sets[a], sets[b])
            if share > best[0]:
                best = (share, a, b)
    return best


def claims_problems(claims: dict, app_repo: Path) -> list[str]:
    """Gate 9: each claim still maps to its code line. Status "unknown" when the repo is not here: never a silent pass."""
    if not app_repo.exists():
        return [f"unknown: app repo {app_repo} is not on this machine, claims not checked"]
    problems = []
    for claim in claims.get("app_note", []):
        for proof in claim.get("also", []):
            lines = (app_repo / proof["file"]).read_text(encoding="utf-8").splitlines()
            if proof["proof"] not in (lines[proof["line"] - 1] if len(lines) >= proof["line"] else ""):
                problems.append(f"gate 9: {claim['text']!r}: {proof['file']}:{proof['line']} no longer holds {proof['proof']!r}")
        if "file" in claim:
            path = app_repo / claim["file"]
            lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
            line = lines[claim["line"] - 1] if len(lines) >= claim["line"] else ""
            if claim["proof"] not in line:
                problems.append(f"gate 9: {claim['text']!r}: {claim['file']}:{claim['line']} no longer holds {claim['proof']!r}")
        elif "dir" in claim:
            count = len(list((app_repo / claim["dir"]).glob("*.ts")))
            if count != claim["count"]:
                problems.append(f"gate 9: {claim['text']!r}: {claim['dir']} has {count} files, the claim says {claim['count']}")
        elif "absent_in" in claim:
            if claim["absent"] in (app_repo / claim["absent_in"]).read_text(encoding="utf-8"):
                problems.append(f"gate 9: {claim['text']!r}: {claim['absent_in']} now holds {claim['absent']!r}")
    return problems
