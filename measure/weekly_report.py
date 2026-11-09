#!/usr/bin/env python3
"""InvoiceSnap's five-line web report (NIK-551, 9 Nov 2026).

Usage: python3 measure/weekly_report.py invoicesnap [--post] [--offline]
Dry run (no Linear comment): omit --post. Load the vault first (source ~/.config/vault/load.sh).
Writes ~/.local/share/invoicesnap-reports/YYYY-Www.{md,json}. Mondays report
last Monday–Sunday; indexing is a separately dated current snapshot. --post
adds the report to NIK-551 through the full-path Linear AXI. No email or AI API.
--offline exercises unavailable sources without credentials or network access.
Exit 3: a live source (GA4, URL Inspection, App Store Connect `asc`, RevenueCat
`revenuecat-axi chart`, NIK-594) was unreachable; its line says unknown.
Indexing runs ~/firstmate-invoicesnap/data/nik-542/index-gate.py (outside this repo).
The timer loads the vault; credentials never enter an artifact or an error.
"""
import argparse
import csv
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

HOME = Path.home()
INDEX = HOME / 'firstmate-invoicesnap/data/nik-542/index-gate.py'
OUTPUT = HOME / '.local/share/invoicesnap-reports'
APP_ID = '6759242022'
RC_PROJECT = 'proj852d4bba'


def post(url, data, token=None):
    body = json.dumps(data).encode() if token else urllib.parse.urlencode(data).encode()
    headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token} if token else {}
    with urllib.request.urlopen(urllib.request.Request(url, body, headers), timeout=30) as response:
        return json.load(response)


def visits(start, end, offline):
    source = 'GA4 property 516575048; hostName=invoicesnap.work; property timezone'
    result = {'status': 'unknown', 'source': source}
    if offline:
        return dict(result, reason='offline check')
    try:
        prefix = 'GOOGLE_ANALYTICS_DATA_'
        token = post('https://oauth2.googleapis.com/token', {
            'client_id': os.environ[prefix + 'CLIENT_ID'],
            'client_secret': os.environ[prefix + 'CLIENT_SECRET'],
            'refresh_token': os.environ[prefix + 'REFRESH_TOKEN'],
            'grant_type': 'refresh_token'})['access_token']
        # This property also serves 731labs.net. A host filter prevents a false traffic count (9 Nov 2026).
        data = post('https://analyticsdata.googleapis.com/v1beta/properties/516575048:runReport', {
            'dateRanges': [{'startDate': str(start), 'endDate': str(end)}],
            'metrics': [{'name': 'sessions'}],
            'dimensionFilter': {'filter': {'fieldName': 'hostName',
                'stringFilter': {'matchType': 'EXACT', 'value': 'invoicesnap.work'}}}}, token)
        # With no rows, tag coverage cannot be distinguished from zero traffic; keep it unknown.
        if not isinstance(data.get('metadata'), dict):
            raise ValueError('missing report metadata')
        rows = data.get('rows', [])
        if not rows:
            # GA4 reached, but no rows: zero traffic or a missing tag. Not a source failure (no exit 3).
            return dict(result, reason='no rows: zero traffic or missing tag')
        value = sum(int(row['metricValues'][0]['value']) for row in rows)
        if value < 0:
            raise ValueError('negative sessions')
        return dict(result, status='measured', value=value,
                    timezone=data['metadata'].get('timeZone', 'property timezone'), preliminary=True)
    except Exception as error:
        return dict(result, reason='read failed: ' + type(error).__name__)


def run_json(command, timeout=240):
    """Run a read-only CLI and parse its JSON stdout. Stderr is dropped: it can echo request ids."""
    run = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=True,
                         env=dict(os.environ, ASC_TIMEOUT=os.environ.get('ASC_TIMEOUT', '120s')))
    return json.loads(run.stdout)


def downloads(start, end, offline):
    """First-time downloads, whole app, from the App Store Connect "App Downloads Standard" report.

    Flow (NIK-594, 9 Nov 2026; ~/.claude/refs/asc-cli.md): ONGOING request -> report -> DAILY
    instances -> segments -> TSV. Verified gotchas:
    - Instances overlap: the 8 Nov instance held 6 Nov rows, the 7 Nov one held 5 and 6 Nov.
      Summing all instances double-counts, so identical rows (all columns but Counts) merge and
      keep their largest Counts.
    - Apple makes no instance for a day without downloads, and data lags 1-3 days. The line names
      the last Apple data day; a week that ends after it is preliminary.
    """
    source = 'App Store Connect "App Downloads Standard" report, app 6759242022, whole app'
    result = {'status': 'unknown', 'source': source}
    if offline:
        return dict(result, reason='offline check')
    try:
        # The unit stops the whole run at TimeoutStartSec=900 (review, 9 Nov 2026). Give Apple at most
        # 420 s: an overrun raises TimeoutExpired, so the line says unknown and the run exits 3.
        deadline = time.monotonic() + 420

        def asc(*args):
            left = deadline - time.monotonic()
            if left <= 5:
                raise TimeoutError('App Store Connect budget spent')
            return run_json([str(HOME / '.local/bin/asc'), 'analytics', *args], timeout=min(240, left))

        with tempfile.TemporaryDirectory(dir=HOME / '.cache') as tmp:
            requests = asc('requests', '--app', APP_ID, '--output', 'json')
            request = next(r['id'] for r in requests['data'] if r['attributes']['accessType'] == 'ONGOING')
            view = asc('view', '--request-id', request, '--output', 'json')
            report = next(r for r in view['data'] if r.get('name') == 'App Downloads Standard')
            # The view embeds a possibly truncated instance list; the paginated links list is complete.
            known = {i['id']: i for i in report.get('instances', [])}
            instances = []
            for link in asc('reports', 'links', '--report-id', report['id'], '--paginate')['data']:
                instance = known.get(link['id'])
                if instance is None:
                    attributes = asc('instances', 'view', '--instance-id', link['id'])['data']['attributes']
                    instance = dict(attributes, id=link['id'])
                # processingDate >= data date, so earlier instances cannot hold rows for this week.
                if instance['granularity'] == 'DAILY' and instance['processingDate'] >= str(start):
                    instances.append(instance)
            if not instances:
                return dict(result, reason='no Apple report day for this week yet')
            by_day = {}  # data day -> {row without Counts: largest Counts seen in any instance}
            for instance in sorted(instances, key=lambda i: i['processingDate']):
                segments = asc('instances', 'links', '--instance-id', instance['id'], '--paginate')['data']
                if not segments:
                    raise ValueError('instance without segments')
                for segment in segments:
                    path = Path(tmp) / (instance['id'] + segment['id'] + '.csv.gz')
                    asc('download', '--request-id', request, '--instance-id', instance['id'],
                        '--segment-id', segment['id'], '--decompress', '--output', str(path))
                    with open(path.with_suffix(''), newline='') as handle:
                        for row in csv.DictReader(handle, delimiter='\t'):
                            key = tuple(sorted((k, v) for k, v in row.items() if k != 'Counts'))
                            day = by_day.setdefault(row['Date'], {})
                            day[key] = max(day.get(key, 0), int(row['Counts']))
            latest = max(by_day) if by_day else None
            value = sum(count for day, rows in by_day.items() if str(start) <= day <= str(end)
                        for key, count in rows.items()
                        if dict(key)['Download Type'] == 'First-time download'
                        and dict(key)['App Apple Identifier'] == APP_ID)
            note = f'Apple data through {latest}' if latest else 'no Apple rows yet'
            return dict(result, status='measured', value=f'{value} first-time downloads',
                        note=note + ('; preliminary' if not latest or latest < str(end) else ''))
    except Exception as error:
        return dict(result, reason='read failed: ' + type(error).__name__)


def money(start, end, offline):
    """Revenue, new trials and new paid subscriptions, whole app, from RevenueCat charts (UTC days)."""
    source = f'RevenueCat charts revenue, trials_new, actives_new; project {RC_PROJECT}; whole app'
    result = {'status': 'unknown', 'source': source}
    if offline:
        return dict(result, reason='offline check')
    try:
        # The npm global bin holds revenuecat-axi; the timer loads REVENUECAT_API_KEY from the vault.
        axi = str(HOME / '.npm-global/bin/revenuecat-axi')
        charts = {name: run_json([axi, 'chart', name, '--start', str(start), '--end', str(end),
                                  '--project', RC_PROJECT, '--json'], timeout=120)
                  for name in ('revenue', 'trials_new', 'actives_new')}
        if any(chart['days'] != (end - start).days + 1 for chart in charts.values()):
            raise ValueError('chart does not cover every day')
        revenue = charts['revenue']['totals']
        trials = charts['trials_new']['totals']['New Trials']
        paid = charts['actives_new']['totals']
        # Product changes are existing payers moving plans, not new paid subscriptions.
        new_paid = paid['Total Paid Subscriptions'] - paid['Product Changes']
        incomplete = sum(chart['incomplete_days'] for chart in charts.values())
        value = (f'${revenue["Revenue"]:,.0f} revenue, {revenue["Transactions"]:.0f} transactions, '
                 f'{trials:.0f} new trials, {new_paid:.0f} new paid subscriptions')
        return dict(result, status='measured', value=value,
                    note='UTC' + ('; preliminary' if incomplete else ''))
    except Exception as error:
        return dict(result, reason='read failed: ' + type(error).__name__)


def indexed(offline):
    result = {'status': 'unknown', 'source': 'Google URL Inspection; live invoicesnap.work sitemap'}
    if offline:
        return dict(result, reason='offline check')
    try:
        run = subprocess.run(['python3', str(INDEX), '--dry-run'], capture_output=True,
                             text=True, timeout=240, check=True)
        data = json.loads(run.stdout)
        # Any failed page keeps the count unknown: partial inspections cannot become a complete count.
        if data.get('state') not in ('hold', 'pass') or data.get('unknown') != 0:
            raise ValueError('incomplete inspection')
        if data['pages'] <= 0 or data['checked'] != data['pages'] or not 0 <= data['indexed'] <= data['checked']:
            raise ValueError('incomplete sitemap coverage')
        return dict(result, status='measured', value=data['indexed'], pages=data['pages'], at=data['at'])
    except Exception as error:
        return dict(result, reason='read failed: ' + type(error).__name__)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', choices=['invoicesnap'])
    parser.add_argument('--post', action='store_true')
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--output', type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.offline and args.post:
        parser.error('--offline cannot post to Linear')
    today = dt.datetime.now(dt.timezone.utc).date()
    start = today - dt.timedelta(days=today.weekday() + 7)
    end = start + dt.timedelta(days=6)
    week = start.strftime('%G-W%V')
    window = f'{start}..{end}'
    metrics = {
        'visits': visits(start, end, args.offline),
        # Downloads and money are whole-app totals: no source attributes them to web visits (NIK-594).
        'downloads': downloads(start, end, args.offline),
        'ai_mentions': {'status': 'unknown', 'source': 'geo-playbook/projects/invoicesnap/prompts/v1.yml',
                        'reason': 'no complete weekly measurement; paid API collection disabled'},
        'indexed_pages': indexed(args.offline),
        'money': money(start, end, args.offline),
    }
    lines = []
    for label, key in [('Visits', 'visits'), ('Downloads', 'downloads'), ('AI mentions', 'ai_mentions'),
                       ('Indexed pages', 'indexed_pages'), ('Money', 'money')]:
        metric = metrics[key]
        period = window
        if metric['status'] == 'measured':
            value = str(metric['value'])
            if key == 'indexed_pages':
                value += '/' + str(metric['pages'])
                period = 'snapshot ' + metric['at']
            elif 'note' in metric:
                period += '; ' + metric['note']
            else:
                period += '; ' + metric['timezone'] + '; preliminary'
            text = f'measured {value}'
        else:
            text = 'unknown (' + metric['reason'] + ')'
        lines.append(f'{label}: {text}; {period}; source: {metric["source"]}.')
    report = '\n'.join(lines) + '\n'
    args.output.mkdir(parents=True, exist_ok=True)
    path = args.output / (week + ('.offline' if args.offline else ''))
    path.with_suffix('.offline.md' if args.offline else '.md').write_text(report)
    path.with_suffix('.offline.json' if args.offline else '.json').write_text(json.dumps({
        'week': week, 'window': window, 'generated': dt.datetime.now(dt.timezone.utc).isoformat(),
        'metrics': metrics}, indent=2) + '\n')
    print(report, end='')
    if args.post:
        # Leave a marker only after Linear accepts the comment. Failed delivery retries next run.
        marker = args.output / (week + '.posted')
        if not marker.exists():
            subprocess.run([str(HOME / '.local/bin/linear-axi'), 'issue', 'comment', 'NIK-551',
                            '--body-file', str(path.with_suffix('.md'))], check=True, timeout=300)
            marker.write_text(dt.datetime.now(dt.timezone.utc).isoformat() + '\n')
    # A live source that could not be read fails the run loudly (9 Nov 2026, NIK-551): the report
    # still says unknown, then exit 3 marks the systemd unit failed. The unit sets
    # RestartPreventExitStatus=3, so a dead source does not repeat 27 URL inspections every 5 minutes.
    failed = [key for key, metric in metrics.items() if metric.get('reason', '').startswith('read failed')]
    if failed:
        print('SOURCE UNREACHABLE: ' + ', '.join(f'{key} ({metrics[key]["reason"]})' for key in failed)
              + '. Fix: check the vault credentials, `asc auth status`, `revenuecat-axi`, and index-gate.py, then rerun.', file=sys.stderr)
        raise SystemExit(3)


if __name__ == '__main__':
    main()
