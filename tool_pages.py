#!/usr/bin/env python3
"""Render the free tool pages from data (NIK-547, NIK-548, 8 Nov 2026). build.py calls this; it has no CLI.

Add a page: write data/tool-pages/<id>.json. `type` is one of trade_invoice, trade_estimate, freelancer_invoice, tool,
hub, answer. gate.py lists what each type must hold; the model page is data/tool-pages/plumbing-invoice.json.
Text in any element with data-shared stays out of the sibling overlap check (blueprint: form code, field labels,
app note and privacy line may repeat). Everything else must differ between pages.

Why the form and the paper are rendered here as well as in tool.js: the paper fills the first screen, so rendering it
only from JavaScript moves the layout (bad CLS) and hides the sample from a crawler. gate_browser.py checks that the
server text equals the JavaScript text, so the two cannot drift apart.
"""
from __future__ import annotations

import datetime as dt
import html
import fact_review
import gate
import json
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

e = lambda s: html.escape(str(s if s is not None else ""), quote=True)  # noqa: E731
WEB_APP = "https://app.invoicesnap.work/sign-in?destination=invoices"
APP_STORE = "https://apps.apple.com/app/id6759242022"
FORM_TYPES = {"trade_invoice", "trade_estimate", "freelancer_invoice", "tool"}


def d(value) -> Decimal:
    try:
        return Decimal(str(value).replace(",", ".")) if value not in ("", None) else Decimal(0)
    except Exception:
        return Decimal(0)


def cents(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def money(value, law: dict) -> str:
    f = law["fmt"]
    text = f"{cents(d(value)):,.2f}".replace(",", "\0").replace(".", f["decimal"]).replace("\0", f["thousands"])
    return f"{text} {f['symbol']}" if f["after"] else f"{f['symbol']}{text}"


def number(value) -> str:
    return f"{d(value).quantize(Decimal('0.01')).normalize():f}"


def thousands(value, law: dict) -> str:
    v = d(value).quantize(Decimal("0.01")).normalize()
    text = f"{v:,f}"
    return text.replace(",", "\0").replace(".", law["fmt"]["decimal"]).replace("\0", law["fmt"]["thousands"])


def date(value: str, law: dict) -> str:
    if not value:
        return ""
    y, m, day = value.split("-")
    return law["dateFmt"].replace("Y", y).replace("M", m).replace("D", day)


def add_days(value: str, days) -> str:
    if not value or days in ("", None):
        return ""
    return (dt.date.fromisoformat(value) + dt.timedelta(days=int(d(days)))).isoformat()


def totals(s: dict, cfg: dict, law: dict) -> dict:
    t = {"net": Decimal(0), "labor": Decimal(0), "mat": Decimal(0)}
    for item in s["items"]:
        amount = cents(d(item["q"]) * d(item["r"]))
        t["net"] += amount
        t["labor" if item.get("k") == "L" else "mat"] += amount
    base = t["mat"] if cfg["taxBase"] and s.get("taxOn") == "parts" else t["net"]
    t["tax"] = cents(base * d(s.get("rate")) / 100)
    t["gross"] = t["net"] + t["tax"]
    t["paid"] = cents(d(s.get("paid")))
    t["balance"] = t["gross"] - t["paid"]
    t["deposit"] = cents(t["gross"] * d(s.get("depositPct")) / 100)
    return t


def paper_html(s: dict, cfg: dict, law: dict) -> str:
    """The same document as paperHTML() + compose() in tool.js, for the rate tax type only."""
    u, kind, t = law["ui"], cfg["kind"], totals(s, cfg, law)
    fm = lambda v: money(v, law)  # noqa: E731
    fd = lambda v: date(v, law)  # noqa: E731
    tot = []
    if cfg["split"]:
        tot += [(u["labor_total"], fm(t["labor"]), 0), (u["parts_total"], fm(t["mat"]), 0)]
    tot.append((u["net"], fm(t["net"]), 0))
    if d(s.get("rate")):
        part = f" ({u['parts_short']})" if cfg["taxBase"] and s.get("taxOn") == "parts" else ""
        tot.append((f"{u['taxLine']} {s['rate']}%{part}", fm(t["tax"]), 0))
    tot.append((u["estimate_total"] if kind == "estimate" else u["gross"], fm(t["gross"]), 1))
    sections, due = [], ""
    if kind == "estimate":
        if d(s.get("depositPct")):
            tot.append((f"{u['deposit_line']} {s['depositPct']}%", fm(t["deposit"]), 0))
        meta = [(u["est_no"], s.get("no")), (u["est_date"], fd(s.get("date"))),
                (u["valid_until"], fd(add_days(s.get("date"), s.get("validDays")))), (u["start_date"], fd(s.get("start")))]
        sections += [(u["scope"], s.get("scope")), (u["exclusions"], s.get("exclusions"))]
    else:
        due = add_days(s.get("date"), s.get("dueDays"))
        if t["paid"]:
            tot += [(u["paid_line"], fm(t["paid"]), 0), (u["balance_line"], fm(t["balance"]), 1)]
        if kind == "freelance":
            meta = [(u["inv_no"], s.get("no")), (u["inv_date"], fd(s.get("date"))), (u["project"], s.get("project")), (u["due_date"], fd(due))]
            sections += [(u["rights"], s.get("rights")), (u["late_fee"], s.get("lateFee"))]
        else:
            meta = [(u["inv_no"], s.get("no")), (u["inv_date"], fd(s.get("date"))), (u["service_date"], fd(s.get("svc"))), (u["due_date"], fd(due))]
    biz = [x for x in (s.get("bizName"), s.get("bizAddr"), s.get("bizId"), s.get("bizContact")) if x]
    to = [x for x in (s.get("custName"), s.get("custAddr")) if x]
    if s.get("siteAddr") and s.get("siteAddr") != s.get("custAddr"):
        to.append(f"{u['service_at']}: {s['siteAddr']}")
    out = f'<div class="p-top"><div><b class="p-co">{e(biz[0] if biz else u["your_biz"])}</b>' + "".join(f"<div>{e(x)}</div>" for x in biz[1:])
    out += f'</div><div class="p-title">{e(u["title_estimate"] if kind == "estimate" else u["title_invoice"])}</div></div>'
    out += f'<div class="p-mid"><div><div class="p-lab">{e(u["prepared_for"] if kind == "estimate" else u["bill_to"])}</div>' + "".join(f"<div>{e(x)}</div>" for x in to)
    out += '</div><table class="p-meta">' + "".join(f"<tr><th>{e(a)}</th><td>{e(b)}</td></tr>" for a, b in meta if b) + "</table></div>"
    out += f'<table class="p-items"><thead><tr><th>{e(u["desc"])}</th><th class="n">{e(u["qty"])}</th><th>{e(u["unit"])}</th><th class="n">{e(u["price"])}</th><th class="n">{e(u["amount"])}</th></tr></thead><tbody>'
    for i in s["items"]:
        out += f'<tr><td>{e(i["d"])}</td><td class="n">{thousands(i["q"], law)}</td><td>{e(i.get("u"))}</td><td class="n">{fm(i["r"])}</td><td class="n">{fm(cents(d(i["q"]) * d(i["r"])))}</td></tr>'
    out += '</tbody></table><table class="p-tot">' + "".join(f'<tr{" class=\"b\"" if b else ""}><th>{e(a)}</th><td>{e(c)}</td></tr>' for a, c, b in tot) + "</table>"
    out += "".join(f'<p class="p-note"><b>{e(a)}:</b> {e(b)}</p>' for a, b in sections if b)
    if s.get("terms"):
        out += f'<p class="p-note">{e(s["terms"])}</p>'
    if s.get("pay"):
        out += f'<p class="p-note">{e(u["pay_by"])}: {e(s["pay"])}</p>'
    if kind == "estimate":
        out += f'<p class="p-note p-accept">{e(u["accept_line"])}</p>'
    return out


def form_cfg(page: dict, law: dict) -> dict:
    form = page["form"]
    kind = form["kind"]
    split = form.get("split", kind != "freelance")
    return {"kind": kind, "split": split, "taxBase": form.get("tax_base", split), "law": public_law(law), "sample": form["sample"],
            "slug": page["slug"].replace("/", "-"), "required": form.get("required", default_required(kind)),
            "keepOnClear": ["taxOn", "mode", "kind", "dueDays", "validDays"]}


def default_required(kind: str) -> list[str]:
    if kind == "estimate":
        return ["bizName", "custName", "no", "scope", "items"]
    if kind == "freelance":
        return ["bizName", "custName", "no", "items"]
    return ["bizName", "custName", "no", "svc", "items"]


def public_law(law: dict) -> dict:
    return {k: law[k] for k in ("locale", "currency", "dateFmt", "page", "dir", "fmt", "tax", "notes", "ui")}


def field(law: dict, form: dict, name: str, cls: str = "", typ: str = "", textarea: bool = False, auto: str = "", mode: str = "") -> str:
    label = form.get("labels", {}).get(name) or law["ui"]["fields"][name]
    value = form["sample"].get(name, "")
    attrs = f' data-f="{name}"' + (f' autocomplete="{auto}"' if auto else "") + (f' inputmode="{mode}"' if mode else "") + (f' type="{typ}"' if typ else "")
    control = f"<textarea{attrs}>{e(value)}</textarea>" if textarea else f'<input{attrs} value="{e(value)}">'
    return f'<label{f" class=\"{cls}\"" if cls else ""}><span>{e(label)}</span>{control}</label>'


def tax_fieldset(page: dict, cfg: dict, law: dict) -> str:
    u, form = law["ui"], page["form"]
    if law["tax"]["type"] == "modes":
        radios = "".join(
            f'<label><input type="radio" name="taxmode" data-f="mode" value="{e(k)}"{" checked" if form["sample"].get("mode") == k else ""}>{e(m["label"])}</label>'
            for k, m in law["tax"]["modes"].items())
        return f'<fieldset class="last"><legend>{e(u["fieldsets"]["tax"])}</legend><div class="choice stack">{radios}</div></fieldset>'
    base = ""
    if cfg["taxBase"]:
        on = form["sample"].get("taxOn")
        base = (f'<div><span class="fake-label">{e(u["fields"]["taxOn"])}</span><div class="choice">'
                f'<label><input type="radio" name="taxon" data-f="taxOn" value="parts"{" checked" if on == "parts" else ""}>{e(u["fields"]["taxOn_parts"])}</label>'
                f'<label><input type="radio" name="taxon" data-f="taxOn" value="all"{" checked" if on == "all" else ""}>{e(u["fields"]["taxOn_all"])}</label></div></div>')
    hint = form.get("tax_hint", u["tax_hint"])
    return (f'<fieldset class="last"><legend>{e(u["fieldsets"]["tax"])}</legend><div class="grid2">{field(law, form, "rate", mode="decimal")}{base}</div>'
            f'<p class="hint">{e(hint)}</p></fieldset>')


def form_html(page: dict, cfg: dict, law: dict) -> str:
    form, u, kind = page["form"], law["ui"], cfg["kind"]
    fs = u["fieldsets"]
    f = lambda *a, **k: field(law, form, *a, **k)  # noqa: E731
    biz = (f'<fieldset><legend>{e(fs["business"])}</legend><div class="grid2">{f("bizName", "full", auto="organization")}{f("bizAddr", "full", auto="street-address")}'
           f'{f("bizId")}{f("bizContact")}{f("pay", "full") if kind != "estimate" else ""}</div></fieldset>')
    who = fs["client"] if kind == "freelance" else fs["customer"]
    cust = f'<fieldset><legend>{e(who)}</legend><div class="grid2">{f("custName", "full")}{f("custAddr", "full")}{f("siteAddr", "full") if kind != "freelance" else ""}</div></fieldset>'
    if kind == "estimate":
        docf = (f'<fieldset><legend>{e(fs["document_estimate"])}</legend><div class="grid2">{f("no")}{f("date", typ="date")}{f("validDays", mode="numeric")}{f("start", typ="date")}'
                f'{f("scope", "full", textarea=True)}{f("exclusions", "full", textarea=True)}{f("depositPct", mode="decimal")}<span></span>{f("terms", "full", textarea=True)}</div></fieldset>')
    elif kind == "freelance":
        docf = (f'<fieldset><legend>{e(fs["document_invoice"])}</legend><div class="grid2">{f("no")}{f("date", typ="date")}{f("project")}{f("dueDays", mode="numeric")}'
                f'{f("rights", "full", textarea=True)}{f("lateFee", "full")}{f("paid", mode="decimal")}<span></span>{f("terms", "full", textarea=True)}</div></fieldset>')
    else:
        docf = (f'<fieldset><legend>{e(fs["document_invoice"])}</legend><div class="grid2">{f("no")}{f("date", typ="date")}{f("svc", typ="date")}{f("dueDays", mode="numeric")}'
                f'{f("paid", mode="decimal")}<span></span>{f("terms", "full", textarea=True)}</div></fieldset>')
    rows = ""
    for it in form["sample"]["items"]:
        kind_sel = ""
        if cfg["split"]:
            kind_sel = (f'<label class="k"><span>{e(u["kind"])}</span><select data-i="k"><option value="L"{" selected" if it.get("k") == "L" else ""}>{e(u["labor"])}</option>'
                        f'<option value="M"{" selected" if it.get("k") != "L" else ""}>{e(u["material"])}</option></select></label>')
        rows += (f'<div class="row{"" if cfg["split"] else " nosplit"}"><label class="d"><span>{e(u["desc"])}</span><input data-i="d" value="{e(it["d"])}"></label>'
                 f'<label class="q"><span>{e(u["qty"])}</span><input data-i="q" inputmode="decimal" value="{e(it["q"])}"></label>'
                 f'<label class="u"><span>{e(u["unit"])}</span><input data-i="u" value="{e(it.get("u"))}"></label>'
                 f'<label class="p"><span>{e(u["price"])}</span><input data-i="r" inputmode="decimal" value="{e(it["r"])}"></label>{kind_sel}'
                 f'<button type="button" class="x" aria-label="{e(u["remove"])}">×</button></div>')
    b = u["buttons"]
    lines = (f'<fieldset><legend>{e(form.get("lines_label") or (fs["lines"] if cfg["split"] else fs["lines_plain"]))}</legend><div id="items">{rows}</div>'
             f'<button type="button" class="btn sm" id="add-row">{e(b["add"])}</button><p class="status" id="added" role="status" aria-live="polite"></p></fieldset>')
    top = f'<div class="form-top"><span>{e(u["sample_hint"])}</span><button type="button" class="btn sm" id="clear">{e(b["clear"])}</button></div>'
    return (f'<form class="card" onsubmit="return false" aria-label="{e(page["h1"])}">{top}{biz}{cust}{docf}{lines}{tax_fieldset(page, cfg, law)}</form>')


def tool_block(page: dict, law: dict) -> tuple[str, dict]:
    cfg = form_cfg(page, law)
    u = law["ui"]
    b = u["buttons"]
    actions = (f'<div class="actions"><button type="button" class="btn pri" data-dl="pdf" id="dl-pdf">{e(b["pdf"])}</button>'
               f'<button type="button" class="btn" data-dl="docx" id="dl-docx">{e(b["docx"])}</button><button type="button" class="btn" data-dl="xlsx" id="dl-xlsx">{e(b["xlsx"])}</button>'
               f'<button type="button" class="btn" data-dl="pdf" data-blank id="dl-blank">{e(b["blank"])}</button></div>')
    side = (f'<div class="side">{actions}<div class="paper-shell"><div id="paper" tabindex="0" role="group" aria-label="{e(u["preview"])}">{paper_html(page["form"]["sample"], cfg, law)}</div></div>'
            f'<p class="miss" id="missing" role="status" aria-live="polite"></p></div>')
    return f'<div class="work" data-shared>{form_html(page, cfg, law)}{side}</div>', cfg


def src_link(ctx, src_id: str, text: str | None = None) -> str:
    s = ctx.sources.get(src_id)
    if not s:  # The gate blocks this page with a clear reason. Rendering must not crash first.
        return e(text or src_id)
    return f'<a href="{e(s["url"])}" rel="noopener">{e(text or s["name"])}</a>'


def crumbs(page: dict, ctx) -> tuple[str, list[tuple[str, str]]]:
    trail = [("Home", "/")]
    parent = page.get("parent")
    if parent and parent in ctx.pages_by_id and parent not in ctx.blocked:
        p = ctx.pages_by_id[parent]
        trail.append((p["crumb"], f"/{p['slug']}/"))
    trail.append((page["crumb"], ""))
    links = " / ".join(f'<a href="{e(h)}">{e(n)}</a>' if h else e(n) for n, h in trail)
    return f'<p class="crumbs" data-shared>{links}</p>', trail


def table_html(headers: list[str], rows: list[list[str]], cls: str = "") -> str:
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f'<div class="scroll"><table class="data{(" " + cls) if cls else ""}"><tr>{head}</tr>{body}</table></div>'


def example_html(page: dict, law: dict) -> str:
    ex = page["example"]
    total = Decimal(0)
    rows = []
    for it in ex["items"]:
        amount = cents(d(it["q"]) * d(it["r"]))
        total += amount
        rows.append([e(it["d"]), thousands(it["q"], law), e(it.get("u")), money(it["r"], law), money(amount, law)])
    rows.append([f"<b>{e(ex.get('total_label', 'Total before tax'))}</b>", "", "", "", f"<b>{money(total, law)}</b>"])
    return (f'<section id="example"><h2>{e(ex["heading"])}</h2><div class="prose"><p>{ex["intro_html"]}</p></div>'
            + table_html(["Description", "Qty", "Unit", "Unit price", "Amount"], rows)
            + (f'<div class="prose"><p>{ex["after_html"]}</p></div>' if ex.get("after_html") else "") + "</section>")


def range_text(it: dict, law: dict) -> str:
    if it.get("low") is None:
        return "Your price"
    def m(v):
        text = money(v, law)
        return text[:-3] if text.endswith(".00") else text
    unit = f" per {it['range_unit']}" if it.get("range_unit") else ""
    return f"{m(it['low'])}–{m(it['high'])}{'+' if it.get('plus') else ''}{unit}"


def line_items_html(page: dict, ctx, law: dict) -> str:
    block = page["line_items"]
    add = law["ui"]["buttons"]["add_item"]
    split = form_cfg(page, law)["split"]
    rows = []
    for it in block["items"]:
        payload = json.dumps([it["name"], 1, it["unit"], it.get("sample", ""), it.get("kind", "L") if split else "L"], ensure_ascii=False)
        source = src_link(ctx, it["src"], ctx.sources.get(it["src"], {}).get("short")) if it.get("src") else "No source: use your own price"
        rows.append([e(it["name"]), range_text(it, law), source, f'<button type="button" class="btn sm" aria-label="{e(add)}: {e(it["name"])}" data-add="{e(payload)}">{e(add)}</button>'])
    return (f'<section id="line-items"><h2>{e(block["heading"])}</h2><div class="prose"><p>{block["intro_html"]}</p></div>'
            + table_html(["Line item", block.get("range_label", "Typical range (US)"), "Source", ""], rows)
            + (f'<div class="prose"><p>{block["after_html"]}</p></div>' if block.get("after_html") else "") + "</section>")


def app_note(ctx, page: dict) -> str:
    claims = gate.app_note_text(ctx.claims, page)
    return (f'<section id="app" class="prose" data-shared><h2>When a form is not enough</h2><p>{e(claims)}</p>'
            f'<ul class="links"><li><a href="{WEB_APP}">Open the web app</a></li><li><a href="{APP_STORE}">App Store</a></li></ul></section>')


def privacy_line() -> str:
    return ('<p class="note-law" data-shared>What you type stays in your browser. The form runs in your browser and makes the files on your device. '
            'It does not send form values to us. We count page visits and downloads with Google Analytics. If you close the page, the form data is gone. '
            'The files are PDF, Word and Excel, not an e-invoice format.</p>')


def related_html(page: dict, ctx) -> str:
    ids = page.get("related", [])
    links = []
    for rid in ids:
        if rid in ctx.pages_by_id and rid not in ctx.blocked:
            r = ctx.pages_by_id[rid]
            links.append(f'<li><a href="/{r["slug"]}/">{e(r.get("link_text") or r["h1"])}</a></li>')
    return f'<section id="related" class="prose" data-shared><h2>Related pages</h2><ul class="links">{"".join(links)}</ul></section>'


def sources_html(page: dict, ctx, law: dict) -> str:
    # Every source that a range or a rule cites stands in the list, also when the page data forgot it.
    cited = [i.get("src") for i in (page.get("line_items") or {}).get("items", [])] + [r.get("src") for r in (page.get("rules") or {}).get("items", [])]
    used = [i for i in dict.fromkeys(page.get("source_ids", []) + cited) if i in ctx.sources]
    items = "".join(f'<li>{src_link(ctx, i)}{(", " + e(ctx.sources[i]["page_date"])) if ctx.sources[i].get("page_date") else ""}: {e(ctx.sources[i]["used_for"])} Checked {e(date(ctx.sources[i]["checked"], law))}.</li>' for i in used)
    checked = date(page["checked"], law)
    # Nikita's order, 9 Nov 2026: never a human name, and an honest word on how the page was checked. gate.py enforces both.
    return (f'<section id="sources" class="prose"><h2>Sources and how this page was checked</h2><p data-shared>Published by 731labs, the maker of InvoiceSnap. Last checked {e(checked)}. '
            f'An AI tool helped draft this page. An automated check compared the legal facts and price ranges with their sources, using two AI models. No person reviewed this page. '
            f'Each number links to its source. This page is general information. It is not tax or legal advice.</p><ul class="src">{items}</ul></section>')


def rules_html(page: dict, ctx) -> str:
    block = page["rules"]
    items = "".join(f'<h3>{e(r["title"])}</h3><p>{r["html"]} {src_link(ctx, r["src"], ctx.sources.get(r["src"], {}).get("short")) if r.get("src") else ""}</p>' for r in block["items"])
    return f'<section id="rules" class="prose"><h2>{e(block["heading"])}</h2>{items}</section>'


def faq_html(page: dict) -> str:
    if not page.get("faq"):
        return ""
    return (f'<section id="faq" class="prose"><h2>{e(page.get("faq_heading", "Questions"))}</h2>' + "".join(f"<h3>{e(q['q'])}</h3><p>{e(q['a'])}</p>" for q in page["faq"]) + "</section>")


def must_show_html(page: dict, law: dict) -> str:
    block = page["must_show"]
    rows = [[e(a), b] for a, b in block["rows"]]
    # Blueprint: an estimate page states the local word and its legal meaning. The words differ per country, so the law record holds them.
    word = f'<p data-shared>{law["estimate_word_html"]}</p>' if page["type"] == "trade_estimate" and law.get("estimate_word_html") else ""
    return (f'<section id="must-include" class="prose"><h2>{e(block["heading"])}</h2><p>{block["intro_html"]}</p>'
            + table_html(["Field", block.get("why_label", "Why it matters")], rows) + word + "</section>")


def bar_html(law: dict) -> str:
    return (f'<div class="bar"><div><small>{e(law["ui"]["total_small"])}</small><b data-total></b></div>'
            f'<button type="button" class="btn pri" data-dl="pdf">{e(law["ui"]["buttons"]["pdf"])}</button></div>')


def render_body(page: dict, ctx, law: dict) -> tuple[str, str, list[tuple[str, str]]]:
    """Return main HTML, the HTML after the wrapper (bar and scripts) and the breadcrumb trail."""
    crumb_html, trail = crumbs(page, ctx)
    typ = page["type"]
    out = [crumb_html, f"<h1>{e(page['h1'])}</h1>", f'<p class="lede">{page["lede_html"]}</p>']
    after = ""
    if typ in FORM_TYPES:
        block, cfg = tool_block(page, law)
        out.append(block)
        out.append(privacy_line())
        cfg_json = json.dumps(cfg, ensure_ascii=False).replace("<", "\\u003c")
        after = bar_html(law) + f'<script type="application/json" id="tool-cfg">{cfg_json}</script><script src="/assets/tool.js" defer></script>'
    if typ in ("trade_invoice", "trade_estimate", "freelancer_invoice"):
        out += [must_show_html(page, law), example_html(page, law), line_items_html(page, ctx, law), rules_html(page, ctx), faq_html(page),
                app_note(ctx, page), related_html(page, ctx), sources_html(page, ctx, law)]
    elif typ == "tool":
        out.append(f'<section class="prose">{page["body_html"]}</section>')
        out += [app_note(ctx, page), related_html(page, ctx), sources_html(page, ctx, law)]
    elif typ == "hub":
        rows = []
        for row in page["rows"]:
            target = ctx.pages_by_id.get(row["page"])
            if target and row["page"] not in ctx.blocked:
                rows.append([f'<a href="/{target["slug"]}/">{e(target.get("link_text") or target["h1"])}</a>', row["html"]])
        out.append('<section id="pages">' + f'<h2>{e(page["table_heading"])}</h2>' + table_html(page["table_headers"], rows, "hub") + "</section>")
        out.append(f'<section class="prose">{page["body_html"]}</section>')
        out += [related_html(page, ctx), sources_html(page, ctx, law)]
    elif typ == "answer":
        out.append(f'<div class="answer">{page["body_html"]}</div>')
        out += [faq_html(page), related_html(page, ctx), sources_html(page, ctx, law)]
    return "".join(out), after, trail


def schema_graph(page: dict, url: str, origin: str, trail: list[tuple[str, str]], law: dict) -> list[dict]:
    graph: list[dict] = []
    if page["type"] in FORM_TYPES:
        graph.append({"@type": "WebApplication", "name": page["h1"], "url": url, "applicationCategory": "BusinessApplication",
                      "operatingSystem": "Any browser", "offers": {"@type": "Offer", "price": "0", "priceCurrency": law["currency"]}})
    if page["type"] == "hub":
        graph.append({"@type": "CollectionPage", "name": page["h1"], "url": url})
    graph.append({"@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": i + 1, "name": n, **({"item": origin + h} if h else {})} for i, (n, h) in enumerate(trail)]})
    return graph


class Context:
    def __init__(self, data_dir: Path) -> None:
        self.dir = data_dir / "tool-pages"
        self.sources = json.loads((data_dir / "sources.json").read_text(encoding="utf-8")) if (data_dir / "sources.json").exists() else {}
        # verify_sources.py writes the status and the real date that it looked at each source. Gate 4 reads both.
        check_file = data_dir / "source-check.json"
        for sid, seen in (json.loads(check_file.read_text(encoding="utf-8")) if check_file.exists() else {}).items():
            if sid in self.sources:
                self.sources[sid].update(status=seen["status"], checked=seen["checked"])
        self.claims = json.loads((data_dir / "claims.json").read_text(encoding="utf-8")) if (data_dir / "claims.json").exists() else {"app_note": []}
        self.demand = json.loads((data_dir / "demand.json").read_text(encoding="utf-8")) if (data_dir / "demand.json").exists() else {}
        # <locale>.review.json and <locale>.fact-sources.json stand beside the law records; a stem with a dot is not a locale.
        self.laws = {f.stem: json.loads(f.read_text(encoding="utf-8")) for f in sorted((data_dir / "law").glob("*.json")) if "." not in f.stem} if (data_dir / "law").exists() else {}
        for law_id, law in self.laws.items():
            law["review_record"] = fact_review.load_record(data_dir, law_id)  # None = missing or broken: gate 5 fails.
        self.pages = [json.loads(f.read_text(encoding="utf-8")) for f in sorted(self.dir.glob("*.json"))] if self.dir.exists() else []
        self.pages_by_id = {p["id"]: p for p in self.pages}
        self.blocked: dict[str, list[str]] = {}
