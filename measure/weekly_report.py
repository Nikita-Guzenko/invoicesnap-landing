#!/usr/bin/env python3
"""InvoiceSnap's five-line web report (NIK-551, 9 Nov 2026).

Usage: python3 measure/weekly_report.py invoicesnap [--post] [--offline]
Writes ~/.local/share/invoicesnap-reports/YYYY-Www.{md,json}. Mondays report
last Monday–Sunday; indexing is a separately dated current snapshot. --post
adds the report to NIK-551 through the full-path Linear AXI. No email or AI API.
--offline exercises unavailable sources without credentials or network access.
The timer loads the vault; credentials never enter an artifact or an error.
"""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import urllib.parse
import urllib.request

HOME = Path.home()
INDEX = HOME / 'firstmate-invoicesnap/data/nik-542/index-gate.py'
OUTPUT = HOME / '.local/share/invoicesnap-reports'


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
        # A successful empty response can mean zero. An API error or malformed response cannot.
        if not isinstance(data.get('metadata'), dict):
            raise ValueError('missing report metadata')
        rows = data.get('rows', [])
        value = sum(int(row['metricValues'][0]['value']) for row in rows)
        if value < 0:
            raise ValueError('negative sessions')
        return dict(result, status='measured', value=value,
                    timezone=data['metadata'].get('timeZone', 'property timezone'), preliminary=True)
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
        if data['checked'] != data['pages'] or not 0 <= data['indexed'] <= data['checked']:
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
        # The existing ASC/RevenueCat totals are whole-app, not attributed to web visits.
        # Until a dated export is connected, keep these unknown rather than reuse an old baseline.
        'downloads': {'status': 'unknown', 'source': 'App Store Connect, app 6759242022',
                      'reason': 'weekly download export is not connected; web attribution unavailable'},
        'ai_mentions': {'status': 'unknown', 'source': 'geo-playbook/projects/invoicesnap/prompts/v1.yml',
                        'reason': 'no complete weekly measurement; paid API collection disabled'},
        'indexed_pages': indexed(args.offline),
        'money': {'status': 'unknown', 'source': 'RevenueCat project proj852d4bba',
                  'reason': 'weekly revenue export is not connected; web attribution unavailable'},
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
                            '--body-file', str(path.with_suffix('.md'))], check=True, timeout=90)
            marker.write_text(dt.datetime.now(dt.timezone.utc).isoformat() + '\n')


if __name__ == '__main__':
    main()
