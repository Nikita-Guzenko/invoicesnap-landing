"""NIK-634: provider failures stay unknown; appended Sheet fields preserve legacy rows."""
import contextlib
import datetime as dt
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('weekly_report', Path(__file__).resolve().parents[1] / 'measure/weekly_report.py')
report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(report)
START, END = dt.date(2026, 11, 9), dt.date(2026, 11, 15)
ENV = {'GOOGLE_ANALYTICS_DATA_CLIENT_ID': 'test', 'GOOGLE_ANALYTICS_DATA_CLIENT_SECRET': 'test',
       'GOOGLE_ANALYTICS_DATA_REFRESH_TOKEN': 'test', 'POSTHOG_READ_KEY': 'secret-test',
       'POSTHOG_PROJECT_ID': '123', 'SENTRY_READ_TOKEN': 'secret-test', 'SENTRY_ORG': 'test',
       'SENTRY_PROJECT_ID': '456'}


class ReportingTests(unittest.TestCase):
    def response(self, request, timeout):
        url = request.full_url
        self.requests.append(request)
        if 'oauth2' in url:
            data = {'access_token': 'test-access'}
        elif 'analyticsdata' in url:
            body = json.loads(request.data)
            self.assertIn('invoicesnap.work', json.dumps(body['dimensionFilter']))
            self.assertIn('app.invoicesnap.work', json.dumps(body['dimensionFilter']))
            data = {'metadata': {'timeZone': 'UTC'}, 'rows': [
                {'dimensionValues': [{'value': 'LCP'}], 'metricValues': [{'value': '3'}]},
                {'dimensionValues': [{'value': 'CLS'}], 'metricValues': [{'value': '2'}]}]}
        elif 'posthog.com' in url:
            sql = json.loads(request.data)['query']['query']
            self.assertIn("properties.$host IN ('invoicesnap.work', 'app.invoicesnap.work')", sql)
            self.assertIn("timestamp < '2026-11-16'", sql)
            data = {'results': [[7]]}
        else:
            self.assertIn('project=456', url)
            self.assertIn('app.invoicesnap.work', url)
            data = {'data': [{'count()': 0}]}
        return contextlib.closing(io.BytesIO(json.dumps(data).encode()))

    def setUp(self):
        self.requests = []

    def test_provider_boundary_counts_and_missing_samples(self):
        with patch.dict(os.environ, ENV, clear=True), patch('urllib.request.urlopen', self.response):
            data = report.web_observability(START, END, False)
        self.assertEqual(data['posthog_events']['count'], 7)
        self.assertEqual(data['sentry_web_errors']['count'], 0)
        self.assertEqual(data['ga4_lcp_samples']['count'], 3)
        self.assertEqual(data['ga4_inp_samples']['status'], 'unknown')
        self.assertNotIn('count', data['ga4_inp_samples'])

    def test_provider_failure_is_unknown_without_secrets(self):
        with patch.dict(os.environ, ENV, clear=True), patch('urllib.request.urlopen', side_effect=OSError('secret-test')):
            data = report.web_observability(START, END, False)
        for metric in data.values():
            self.assertEqual(metric['status'], 'unknown')
            self.assertTrue(metric['reason'].startswith('read failed:'))
            self.assertNotIn('count', metric)
            self.assertNotIn('secret-test', metric['reason'])

    def test_missing_access_never_calls_posthog_or_sentry(self):
        with patch.dict(os.environ, {k: v for k, v in ENV.items() if k.startswith('GOOGLE_')}, clear=True), patch('urllib.request.urlopen', self.response):
            data = report.web_observability(START, END, False)
        self.assertIn('POSTHOG_READ_KEY', data['posthog_events']['reason'])
        self.assertIn('SENTRY_READ_TOKEN', data['sentry_web_errors']['reason'])
        self.assertFalse(any('posthog.com' in r.full_url or 'sentry.io' in r.full_url for r in self.requests))

    def test_historical_and_offline_do_not_query(self):
        with patch('urllib.request.urlopen', side_effect=AssertionError('unexpected network')):
            for data in [report.web_observability(START, END, True),
                         report.web_observability(dt.date(2026, 11, 2), dt.date(2026, 11, 8), False)]:
                self.assertTrue(all(m['status'] == 'unknown' for m in data.values()))

    def test_real_cli_offline_artifact_has_each_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            command = ['python3', str(Path(report.__file__)), 'invoicesnap', '--offline', '--no-sheet', '--output', tmp]
            run = subprocess.run(command, capture_output=True, text=True, check=True)
            self.assertIn('PostHog web events: unknown (offline check)', run.stdout)
            self.assertIn('Sentry web errors: unknown (offline check)', run.stdout)
            self.assertIn('GA4 INP samples: unknown (offline check)', run.stdout)
            artifact = json.loads(next(Path(tmp).glob('*.json')).read_text())
            self.assertEqual(artifact['metrics']['ga4_lcp_samples']['status'], 'unknown')

    def test_old_sheet_columns_stable_and_upsert_keeps_snapshot(self):
        names = [name for name, _ in report.SHEET_COLUMNS]
        self.assertEqual(names[18:20], ['unknown', 'generated_utc'])
        self.assertEqual(names[20:], ['posthog_events', 'sentry_web_errors', 'ga4_lcp_samples', 'ga4_inp_samples', 'ga4_cls_samples'])
        metrics = {ref[0]: {'status': 'unknown', 'reason': 'not ready'} for _, ref in report.SHEET_COLUMNS if ref}
        metrics['indexed_pages']['reason'] = 'backfill: current snapshot only'
        calls = []
        def cli(command, **kwargs):
            calls.append(command)
            output = {'values': [['week'], ['2026-W46']]} if 'get' in command else {}
            return subprocess.CompletedProcess(command, 0, json.dumps(output), '')
        with patch('subprocess.run', cli):
            row = report.write_sheet('2026-W46', START, END, metrics, 'now')
        self.assertEqual(row, 2)
        last = calls[-1]
        payload = json.loads(last[last.index('--json') + 1])['values'][0]
        self.assertIsNone(payload[17])
        self.assertEqual(payload[20:], ['', '', '', '', ''])
        self.assertIn('posthog_events: not ready', payload[18])
        self.assertEqual(json.loads(last[last.index('--params') + 1])['range'], 'weekly!A2')


if __name__ == '__main__':
    unittest.main()
