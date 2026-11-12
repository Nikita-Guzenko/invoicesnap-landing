#!/usr/bin/env python3
"""InvoiceSnap's five-line web report (NIK-551, 9 Nov 2026).

Usage: python3 measure/weekly_report.py invoicesnap [--post] [--offline] [--start YYYY-MM-DD] [--no-sheet]
Dry run (no Linear comment): omit --post. Load the vault first (source ~/.config/vault/load.sh).
--start reports the week that starts on that Monday (backfill; cannot --post).
Each run also writes the week's numbers to the Google Sheet SHEET_ID, tab "weekly" (NIK-634), through
`gws` (logged in as kazakdstr@gmail.com). The Looker Studio dashboard DASHBOARD reads that Sheet plus
the GA4 and Search Console connectors. A blank cell means unknown, never 0. --no-sheet skips the write.
Writes ~/.local/share/invoicesnap-reports/YYYY-Www.{md,json}. Mondays report
last Monday–Sunday; indexing is a separately dated current snapshot. --post
adds the report to NIK-551 through the full-path Linear AXI. No email or AI API.
--offline exercises unavailable sources without credentials or network access.
Exit 3: a live source (GA4, Search Console, URL Inspection, App Store Connect `asc`, RevenueCat
`revenuecat-axi chart`, NIK-594) was unreachable, or the Sheet write failed; its line says unknown.
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
GA_PROPERTY = '516575048'
SITE_HOST, APP_HOST = 'invoicesnap.work', 'app.invoicesnap.work'
# GA4 funnel events start on 12 Nov 2026 (NIK-634): tool_used on the site, every web app event.
# An earlier week has no such events because they did not exist, so it says unknown, not 0.
FUNNEL_SINCE = dt.date(2026, 11, 12)
SHEET_ID = '1G1ijIEeZqvGLY3CUa4qd65HD7-d2dfXdD1qiAgvdY1Y'
DASHBOARD = 'https://lookerstudio.google.com/reporting/80fe415d-f48f-449d-ab52-9b3ffb5516dc'


def post(url, data, token=None):
    body = json.dumps(data).encode() if token else urllib.parse.urlencode(data).encode()
    headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token} if token else {}
    with urllib.request.urlopen(urllib.request.Request(url, body, headers), timeout=30) as response:
        return json.load(response)


def google_token(prefix):
    """An access token from a vault OAuth refresh token (GOOGLE_ANALYTICS_DATA_, GOOGLE_SEARCH_CONSOLE_)."""
    return post('https://oauth2.googleapis.com/token', {
        'client_id': os.environ[prefix + 'CLIENT_ID'],
        'client_secret': os.environ[prefix + 'CLIENT_SECRET'],
        'refresh_token': os.environ[prefix + 'REFRESH_TOKEN'],
        'grant_type': 'refresh_token'})['access_token']


def ga_report(body):
    token = google_token('GOOGLE_ANALYTICS_DATA_')
    return post(f'https://analyticsdata.googleapis.com/v1beta/properties/{GA_PROPERTY}:runReport', body, token)


def visits(start, end, offline, host=SITE_HOST):
    source = f'GA4 property {GA_PROPERTY}; hostName={host}; property timezone'
    result = {'status': 'unknown', 'source': source}
    if offline:
        return dict(result, reason='offline check')
    if host == APP_HOST and end < FUNNEL_SINCE:
        return dict(result, reason=f'web app not tagged before {FUNNEL_SINCE}')
    try:
        if host == APP_HOST and not live_since(end)['app']:
            return dict(result, reason=f'no hit from the web app tag since {FUNNEL_SINCE}')
        # This property also serves 731labs.net. A host filter prevents a false traffic count (9 Nov 2026).
        data = ga_report({
            'dateRanges': [{'startDate': str(start), 'endDate': str(end)}],
            'metrics': [{'name': 'sessions'}],
            'dimensionFilter': {'filter': {'fieldName': 'hostName',
                'stringFilter': {'matchType': 'EXACT', 'value': host}}}})
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
        out = dict(result, status='measured', value=value, count=value,
                   timezone=data['metadata'].get('timeZone', 'property timezone'), preliminary=True)
        if host == APP_HOST and start < FUNNEL_SINCE:
            out['note'] = out['timezone'] + f'; preliminary; app tag starts {FUNNEL_SINCE}'
        return out
    except Exception as error:
        return dict(result, reason='read failed: ' + type(error).__name__)


def live_since(end):
    """Which NIK-634 tags have sent anything since FUNNEL_SINCE: {'app': bool, 'tool_used': bool}.

    A count of 0 is true only after its tag is live. Before the first hit (deploy day, ad blockers,
    a broken tag) the line says unknown (review, 12 Nov 2026).
    """
    data = ga_report({
        'dateRanges': [{'startDate': str(FUNNEL_SINCE), 'endDate': str(end)}],
        'dimensions': [{'name': 'hostName'}, {'name': 'eventName'}],
        'metrics': [{'name': 'eventCount'}],
        'dimensionFilter': {'filter': {'fieldName': 'hostName', 'inListFilter': {'values': [SITE_HOST, APP_HOST]}}}})
    if not isinstance(data.get('metadata'), dict):
        raise ValueError('missing report metadata')
    seen = {(r['dimensionValues'][0]['value'], r['dimensionValues'][1]['value']) for r in data.get('rows', [])}
    return {'app': any(host == APP_HOST for host, _ in seen),
            'tool_used': any(event == 'tool_used' for _, event in seen)}


FUNNEL_EVENTS = {  # GA4 key events (NIK-634) -> report key
    'tool_used': 'tool_used', 'sign_up': 'web_signups', 'paywall_view': 'paywall_views',
    'checkout_start': 'checkout_starts'}


def funnel(start, end, offline):
    """GA4 funnel key events on both InvoiceSnap hosts, one metric per event."""
    source = (f'GA4 property {GA_PROPERTY} key event %s; hostName={SITE_HOST} or {APP_HOST}; '
              'property timezone')
    out = {key: {'status': 'unknown', 'source': source % event} for event, key in FUNNEL_EVENTS.items()}
    if offline:
        return {key: dict(m, reason='offline check') for key, m in out.items()}
    if end < FUNNEL_SINCE:
        return {key: dict(m, reason=f'event not sent before {FUNNEL_SINCE}') for key, m in out.items()}
    try:
        data = ga_report({
            'dateRanges': [{'startDate': str(start), 'endDate': str(end)}],
            'dimensions': [{'name': 'eventName'}],
            'metrics': [{'name': 'eventCount'}],
            'dimensionFilter': {'andGroup': {'expressions': [
                {'filter': {'fieldName': 'hostName', 'inListFilter': {'values': [SITE_HOST, APP_HOST]}}},
                {'filter': {'fieldName': 'eventName', 'inListFilter': {'values': list(FUNNEL_EVENTS)}}}]}}})
        if not isinstance(data.get('metadata'), dict):
            raise ValueError('missing report metadata')
        counts = {row['dimensionValues'][0]['value']: int(row['metricValues'][0]['value'])
                  for row in data.get('rows', [])}
        live = live_since(end)
        note = data['metadata'].get('timeZone', 'property timezone') + '; preliminary'
        if start < FUNNEL_SINCE:
            note += f'; events start {FUNNEL_SINCE}'
        result = {}
        for event, key in FUNNEL_EVENTS.items():
            # A missing row is a measured 0 only once its tag has sent a hit (see live_since).
            if event not in counts and not live['tool_used' if event == 'tool_used' else 'app']:
                where = 'invoicesnap.work tool_used' if event == 'tool_used' else 'web app tag'
                result[key] = dict(out[key], reason=f'no hit from the {where} since {FUNNEL_SINCE}')
            else:
                result[key] = dict(out[key], status='measured', value=counts.get(event, 0),
                                   count=counts.get(event, 0), note=note)
        return result
    except Exception as error:
        return {key: dict(m, reason='read failed: ' + type(error).__name__) for key, m in out.items()}


def search(start, end, offline):
    """Google Search clicks and impressions for the whole domain property (data lags 2-3 days)."""
    site = 'sc-domain:invoicesnap.work'
    result = {'status': 'unknown', 'source': f'Search Console {site}, web search, UTC'}
    if offline:
        return dict(result, reason='offline check')
    try:
        # The vault OAuth token owns sc-domain:invoicesnap.work (read-only); the service account owns
        # only the URL-prefix property that index-gate.py inspects.
        token = google_token('GOOGLE_SEARCH_CONSOLE_')
        data = post('https://www.googleapis.com/webmasters/v3/sites/' + urllib.parse.quote(site, safe='')
                    + '/searchAnalytics/query',
                    {'startDate': str(start), 'endDate': str(end), 'dimensions': ['date'], 'dataState': 'all'},
                    token)
        rows = data.get('rows', [])
        if not rows:
            # No row for any day: Google has no data for this window yet (lag), not 0 searches.
            return dict(result, reason='no rows yet (Search Console data lag)')
        clicks = sum(int(r['clicks']) for r in rows)
        impressions = sum(int(r['impressions']) for r in rows)
        last = max((r['keys'][0] for r in rows), default=None)
        note = f'data through {last}'
        if last < str(end):
            note += '; preliminary'
        return dict(result, status='measured', value=f'{clicks} clicks, {impressions} impressions',
                    clicks=clicks, impressions=impressions, note=note)
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
            return dict(result, status='measured', value=f'{value} first-time downloads', count=value,
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
        return dict(result, status='measured', value=value, revenue=round(revenue['Revenue'], 2),
                    transactions=int(revenue['Transactions']), trials=int(trials), new_paid=int(new_paid),
                    note='UTC' + ('; preliminary' if incomplete else ''))
    except Exception as error:
        return dict(result, reason='read failed: ' + type(error).__name__)


def web_purchases(start, end, offline):
    """New paid web subscriptions (RevenueCat Web Billing, store rc_billing), production only (NIK-552)."""
    source = f'RevenueCat chart actives_new, filter store=rc_billing; project {RC_PROJECT}'
    result = {'status': 'unknown', 'source': source}
    if offline:
        return dict(result, reason='offline check')
    try:
        # revenuecat-axi chart has no filter flag, so call the same Charts API v2 with the store filter.
        # Sandbox test purchases are excluded: the API returns production data unless sandbox_mode=true.
        query = urllib.parse.urlencode({
            'start_date': str(start), 'end_date': str(end), 'resolution': '0',
            'filters': json.dumps([{'name': 'store', 'values': ['rc_billing']}])})
        request = urllib.request.Request(
            f'https://api.revenuecat.com/v2/projects/{RC_PROJECT}/charts/actives_new?{query}',
            headers={'Authorization': 'Bearer ' + os.environ['REVENUECAT_API_KEY']})
        with urllib.request.urlopen(request, timeout=120) as response:
            chart = json.load(response)
        # An ignored filter would count the whole app as "web": fail instead (Claude review, 10 Nov 2026).
        if 'filters' in (chart.get('unsupported_params') or []):
            raise ValueError('store filter not applied')
        days = {row['cohort'] for row in chart['values']}
        if len(days) != (end - start).days + 1:
            raise ValueError('chart does not cover every day')
        incomplete = any(row.get('incomplete') for row in chart['values'])
        total = chart['summary']['total']
        # Product changes are existing payers moving plans, not new purchases.
        value = total['Total Paid Subscriptions'] - total['Product Changes']
        return dict(result, status='measured', value=f'{value:.0f} new web subscriptions', count=int(value),
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


SHEET_COLUMNS = [  # tab "weekly", one row per week; the Looker Studio dashboard reads these names
    ('week', None), ('week_start', None), ('week_end', None),
    ('site_visits', ('visits', 'count')), ('app_visits', ('app_visits', 'count')),
    ('tool_used', ('tool_used', 'count')), ('web_signups', ('web_signups', 'count')),
    ('paywall_views', ('paywall_views', 'count')), ('checkout_starts', ('checkout_starts', 'count')),
    ('web_purchases', ('web_purchases', 'count')),
    ('search_clicks', ('search', 'clicks')), ('search_impressions', ('search', 'impressions')),
    ('app_downloads', ('downloads', 'count')), ('new_trials', ('money', 'trials')),
    ('new_paid_subscriptions', ('money', 'new_paid')), ('revenue_usd', ('money', 'revenue')),
    ('transactions', ('money', 'transactions')), ('indexed_pages', ('indexed_pages', 'value')),
    ('unknown', None), ('generated_utc', None)]


def gws(*args, body=None):
    """Run the gws CLI (Google Workspace, logged in as kazakdstr@gmail.com) and parse its JSON."""
    command = [str(HOME / '.local/bin/gws'), *args]
    if body is not None:
        command += ['--json', json.dumps(body)]
    # 30 s per call, 3 calls: the Sheet cannot eat the unit's TimeoutStartSec=900 budget (review, 12 Nov 2026).
    run = subprocess.run(command, capture_output=True, text=True, timeout=30, check=True)
    return json.loads(run.stdout[run.stdout.index('{'):])


def write_sheet(week, start, end, metrics, generated):
    """Upsert this week's row. Unknown metrics stay blank and are named in the `unknown` column."""
    row = []
    for name, ref in SHEET_COLUMNS:
        if ref is None:
            row.append({'week': week, 'week_start': str(start), 'week_end': str(end), 'generated_utc': generated,
                        'unknown': '; '.join(f'{k}: {m["reason"]}' for k, m in metrics.items()
                                             if m['status'] != 'measured')}[name])
        else:
            metric = metrics[ref[0]]
            if metric.get('reason', '').startswith('backfill'):
                row.append(None)  # a snapshot column on a backfill: keep what the Sheet holds
            else:
                row.append(metric.get(ref[1], '') if metric['status'] == 'measured' else '')
    header = [name for name, _ in SHEET_COLUMNS]
    gws('sheets', 'spreadsheets', 'values', 'update', '--params', json.dumps(
        {'spreadsheetId': SHEET_ID, 'range': 'weekly!A1', 'valueInputOption': 'RAW'}), body={'values': [header]})
    weeks = gws('sheets', 'spreadsheets', 'values', 'get', '--params', json.dumps(
        {'spreadsheetId': SHEET_ID, 'range': 'weekly!A:A'})).get('values', [])
    found = next((n for n, cells in enumerate(weeks, 1) if cells and cells[0] == week), None)
    target = found or len(weeks) + 1
    # USER_ENTERED turns week_start/week_end into Sheet dates, so Looker Studio reads a Date field.
    gws('sheets', 'spreadsheets', 'values', 'update', '--params', json.dumps(
        {'spreadsheetId': SHEET_ID, 'range': f'weekly!A{target}', 'valueInputOption': 'USER_ENTERED'}),
        body={'values': [row]})
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('project', choices=['invoicesnap'])
    parser.add_argument('--post', action='store_true')
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--start', type=dt.date.fromisoformat, help='Monday of the week to report (backfill)')
    parser.add_argument('--no-sheet', action='store_true', help='do not write the Google Sheet')
    parser.add_argument('--output', type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.offline and args.post:
        parser.error('--offline cannot post to Linear')
    if args.start and args.post:
        parser.error('--start is a backfill: it cannot post to Linear')
    if args.start and args.start.weekday() != 0:
        parser.error('--start must be a Monday')
    today = dt.datetime.now(dt.timezone.utc).date()
    start = args.start or today - dt.timedelta(days=today.weekday() + 7)
    end = start + dt.timedelta(days=6)
    week = start.strftime('%G-W%V')
    window = f'{start}..{end}'
    metrics = {
        'visits': visits(start, end, args.offline),
        'app_visits': visits(start, end, args.offline, host=APP_HOST),
        **funnel(start, end, args.offline),
        'search': search(start, end, args.offline),
        # Downloads and money are whole-app totals: no source attributes them to web visits (NIK-594).
        'downloads': downloads(start, end, args.offline),
        'ai_mentions': {'status': 'unknown', 'source': 'geo-playbook/projects/invoicesnap/prompts/v1.yml',
                        'reason': 'no complete weekly measurement; paid API collection disabled'},
        # Indexing is a current snapshot: a backfilled week cannot know its old value.
        'indexed_pages': (indexed(args.offline) if not args.start else
                          {'status': 'unknown', 'source': 'Google URL Inspection',
                           'reason': 'backfill: indexing is a current snapshot only'}),
        'money': money(start, end, args.offline),
        'web_purchases': web_purchases(start, end, args.offline),
    }
    lines = []
    for label, key in [('Visits', 'visits'), ('Web app visits', 'app_visits'), ('Free tool use', 'tool_used'),
                       ('Web sign-ups', 'web_signups'), ('Paywall views', 'paywall_views'),
                       ('Checkout starts', 'checkout_starts'), ('Search', 'search'),
                       ('Downloads', 'downloads'), ('AI mentions', 'ai_mentions'),
                       ('Indexed pages', 'indexed_pages'), ('Money', 'money'),
                       ('Web purchases', 'web_purchases')]:
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
    generated = dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
    sheet_error = None
    if not args.offline and not args.no_sheet:
        try:
            write_sheet(week, start, end, metrics, generated)
        except Exception as error:
            sheet_error = type(error).__name__
    dashboard = '' if 'LOOKER_REPORT_ID' in DASHBOARD else (
        f'{DASHBOARD} (funnel, weekly trend, top pages, search queries, App Store and RevenueCat); ')
    lines.append(f'Dashboard: {dashboard}data sheet https://docs.google.com/spreadsheets/d/{SHEET_ID}'
                 + (f' — this week NOT written ({sheet_error})' if sheet_error else '') + '.')
    report = '\n'.join(lines) + '\n'
    args.output.mkdir(parents=True, exist_ok=True)
    path = args.output / (week + ('.offline' if args.offline else ''))
    path.with_suffix('.offline.md' if args.offline else '.md').write_text(report)
    path.with_suffix('.offline.json' if args.offline else '.json').write_text(json.dumps({
        'week': week, 'window': window, 'generated': generated, 'metrics': metrics}, indent=2) + '\n')
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
    if failed or sheet_error:
        print('SOURCE UNREACHABLE: ' + ', '.join(f'{key} ({metrics[key]["reason"]})' for key in failed)
              + (f' sheet write ({sheet_error})' if sheet_error else '')
              + '. Fix: check the vault credentials, `asc auth status`, `revenuecat-axi`, `gws auth status`'
              ' and index-gate.py, then rerun.', file=sys.stderr)
        raise SystemExit(3)


if __name__ == '__main__':
    main()
