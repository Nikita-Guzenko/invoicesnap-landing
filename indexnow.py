#!/usr/bin/env python3
"""Tell IndexNow (Bing, Yandex and others) which URLs changed or went away (playbook rule R03, NIK-545, 8 Nov 2026).

Usage: python3 indexnow.py     publish.py runs it after production passes the crawl.

It compares data/lastmod.json (a content hash per URL) with the state of the last accepted submit, so a failed
run sends its URLs on the next run, and a second text change on one day is not lost. No state file: it sends every URL.
State and answer log: ~/.local/state/invoicesnap/indexnow.json and indexnow.log (playbook R03 asks for the answers).

Google does not use IndexNow; it reads the sitemap named in robots.txt. A non-2xx answer exits 1 with the answer text.
403 means IndexNow could not read the key file: check https://invoicesnap.work/<key>.txt. 429 means too many
requests: run it again later.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE_DIR = Path.home() / ".local" / "state" / "invoicesnap"
BATCH = 10000  # IndexNow limit for one request.


def log(line: str) -> None:
    print(line)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with (STATE_DIR / "indexnow.log").open("a", encoding="utf-8") as f:
        f.write(f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%dT%H:%M:%SZ} {line}\n")


def main() -> int:
    if len(sys.argv) > 1:
        print(__doc__)
        return 2
    config = json.loads((ROOT / "data" / "site.json").read_text(encoding="utf-8"))
    origin, key = config["origin"], config["indexnow_key"]
    now = {path: row["hash"] for path, row in json.loads((ROOT / "data" / "lastmod.json").read_text(encoding="utf-8")).items()}
    state_file = STATE_DIR / "indexnow.json"
    before = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {}
    changed = sorted(p for p in set(now) | set(before) if now.get(p) != before.get(p))  # Removed URLs go too: they answer 404.
    log(f"{len(changed)} of {len(now)} URLs are new, changed or removed")
    for start in range(0, len(changed), BATCH):
        urls = [origin + p for p in changed[start:start + BATCH]]
        body = {"host": origin.removeprefix("https://"), "key": key, "keyLocation": f"{origin}/{key}.txt", "urlList": urls}
        request = urllib.request.Request("https://api.indexnow.org/indexnow", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json; charset=utf-8"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                log(f"IndexNow answer: HTTP {response.status} for {len(urls)} URLs")
        except (urllib.error.URLError, OSError) as e:
            detail = e.read()[:300].decode("utf-8", "replace") if isinstance(e, urllib.error.HTTPError) else ""
            log(f"IndexNow answer: {e} {detail}")
            return 1
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(now), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
