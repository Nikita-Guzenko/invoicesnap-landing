#!/usr/bin/env python3
"""Event list check (NIK-634, 12 Nov 2026). Standard library only.

Usage: python3 tests/test_events.py [root]
Fails (exit 1) when:
- the code sends an event (gtag('event', 'x'), trackEvent('x'), track('x'), posthog.capture('x'),
  trackToolUsed -> tool_used) that data/events.json does not list under "sent";
- data/events.json lists a sent event that no code sends any more;
- the consent banner in src/static/assets/ga.js has no text for a locale tag in data/locales.json.
Fix: add the event to data/events.json (where it fires, its properties), or remove the stale entry.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
SEND = re.compile(r"""(?:gtag\(\s*['"]event['"]\s*,|trackEvent\(|\btrack\(|posthog\.capture\()\s*['"]([A-Za-z0-9_$]+)['"]""")


def sent_in_code():
    found = {}
    for base in ('src', 'templates'):
        for path in (ROOT / base).rglob('*'):
            if path.suffix not in ('.js', '.html') or path.name == 'web-vitals.iife.js':
                continue
            text = path.read_text(encoding='utf-8')
            for name in SEND.findall(text):
                found.setdefault(name, path.relative_to(ROOT).as_posix())
            if 'trackToolUsed(' in text:
                found.setdefault('tool_used', path.relative_to(ROOT).as_posix())
    return found


def banner_locales():
    ga = (ROOT / 'src/static/assets/ga.js').read_text(encoding='utf-8')
    table = ga[ga.index('var T = {'):ga.index('var ALIAS')]
    keys = set(re.findall(r"^\s*'?([A-Za-z-]+)'?\s*:\s*\[", table, re.M))
    alias = dict(re.findall(r"'([A-Za-z-]+)'\s*:\s*'([A-Za-z-]+)'", ga[ga.index('var ALIAS'):].split('\n')[0]))
    return keys, alias


def main():
    listed = set(json.loads((ROOT / 'data/events.json').read_text())['sent'])
    code = sent_in_code()
    problems = [f'{name} is sent in {where} but not listed in data/events.json'
                for name, where in sorted(code.items()) if name not in listed]
    problems += [f'{name} is listed in data/events.json but no code sends it' for name in sorted(listed - set(code))]
    keys, alias = banner_locales()
    for tag in json.loads((ROOT / 'data/locales.json').read_text()):
        if tag not in keys and alias.get(tag) not in keys:
            problems.append(f'consent banner has no text for locale {tag} (src/static/assets/ga.js T / ALIAS)')
    for p in problems:
        print('FAIL', p)
    print(f'{len(code)} events in code, {len(listed)} listed, {len(problems)} problem(s)')
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
