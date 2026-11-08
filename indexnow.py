#!/usr/bin/env python3
"""Tell IndexNow (Bing, Yandex and others) which pages changed in a deploy (playbook rule R03, NIK-545, 8 Nov 2026).

Usage, in this order (publish.py runs both):
  python3 indexnow.py snapshot before.json      before deploy: read lastmod of each live URL
  python3 indexnow.py submit before.json dist   after deploy: send the URLs that are new or have a new lastmod

Google does not use IndexNow; it reads the sitemap named in robots.txt. A non-2xx answer exits 1 with the
answer text. 403 means IndexNow could not read the key file: check https://invoicesnap.work/<key>.txt. 429 means
too many requests: run it again later. An unreadable live sitemap writes an empty snapshot, so all URLs go out.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
BATCH = 10000  # IndexNow limit for one request.


def rows(xml: str, read) -> dict[str, str]:
    root = ET.fromstring(xml)
    if root.tag.endswith("sitemapindex"):
        result: dict[str, str] = {}
        for loc in root.findall("s:sitemap/s:loc", NS):
            result.update(rows(read(loc.text), read))
        return result
    return {u.findtext("s:loc", "", NS): u.findtext("s:lastmod", "", NS) for u in root.findall("s:url", NS)}


def http_read(url: str) -> str:
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read().decode()


def snapshot(out: Path, origin: str) -> int:
    try:
        data = rows(http_read(f"{origin}/sitemap.xml"), http_read)
    except (OSError, ET.ParseError) as e:
        print(f"could not read the live sitemap ({e}); the next submit sends every URL")
        data = {}
    out.write_text(json.dumps(data))
    print(f"snapshot: {len(data)} URLs")
    return 0


def submit(before_file: Path, dist: Path, origin: str, key: str) -> int:
    before = json.loads(before_file.read_text())
    local = lambda url: (dist / url.removeprefix(origin + "/")).read_text(encoding="utf-8")
    after = rows((dist / "sitemap.xml").read_text(encoding="utf-8"), local)
    changed = sorted(u for u, m in after.items() if before.get(u) != m)
    print(f"{len(changed)} of {len(after)} URLs are new or changed")
    for start in range(0, len(changed), BATCH):
        body = {"host": origin.removeprefix("https://"), "key": key, "keyLocation": f"{origin}/{key}.txt",
                "urlList": changed[start:start + BATCH]}
        request = urllib.request.Request("https://api.indexnow.org/indexnow", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json; charset=utf-8"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                print(f"IndexNow answer: HTTP {response.status} for {len(body['urlList'])} URLs")
        except urllib.error.HTTPError as e:
            print(f"IndexNow answer: HTTP {e.code}: {e.read()[:300].decode('utf-8', 'replace')}", file=sys.stderr)
            return 1
    return 0


def main() -> int:
    config = json.loads((ROOT / "data" / "site.json").read_text(encoding="utf-8"))
    args = sys.argv[1:]
    if len(args) == 2 and args[0] == "snapshot":
        return snapshot(Path(args[1]), config["origin"])
    if len(args) == 3 and args[0] == "submit":
        return submit(Path(args[1]), Path(args[2]), config["origin"], config["indexnow_key"])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
