#!/usr/bin/env python3
"""NIK-634 real SDK regression: build.py, local https server :8738, then python3 tests/test_analytics_browser.py.
Requires Python Playwright/Chromium. --baseline loads ga.js from origin/main's PR head before resume fixes.
Uses real GA/PostHog SDKs and ingest. No provider, delivery, or payload is mocked.
"""
import base64
import glob
import gzip
import json
from pathlib import Path
import subprocess
import sys
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OLD = '--baseline' in sys.argv
SECRET = 'NIK634URLPRIVATE98314'
UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36'
# Capture browser transport bodies before the automation protocol loses Blob-backed bytes.
CAPTURE = """(() => {window.__bodies=[]; const keep=(u,b)=>{if(!b||!String(u).includes('posthog.com'))return;
const p=b instanceof Blob?b.arrayBuffer():Promise.resolve(typeof b==='string'?new TextEncoder().encode(b):b);
p.then(ab=>{const a=new Uint8Array(ab);let s='';for(let i=0;i<a.length;i+=8192)s+=String.fromCharCode.apply(null,a.subarray(i,i+8192));window.__bodies.push(btoa(s));});};
const f=window.fetch;window.fetch=function(u,o){keep(typeof u==='string'?u:u.url,o&&o.body);return f.apply(this,arguments);};
const op=XMLHttpRequest.prototype.open,se=XMLHttpRequest.prototype.send;
XMLHttpRequest.prototype.open=function(m,u){this.__url=u;return op.apply(this,arguments);};XMLHttpRequest.prototype.send=function(b){keep(this.__url,b);return se.apply(this,arguments);};
const sb=navigator.sendBeacon.bind(navigator);navigator.sendBeacon=(u,b)=>{keep(u,b);return sb(u,b);};})()"""

def decoded(raw):
    if raw[:2] == b'\x1f\x8b': raw=gzip.decompress(raw)
    return raw.decode('utf-8','replace')

with sync_playwright() as p:
    exe=glob.glob(str(Path.home()/'.cache/ms-playwright/chromium-1200/chrome-linux*/chrome'))[0]
    browser=p.chromium.launch(executable_path=exe,args=['--host-resolver-rules=MAP invoicesnap.work 127.0.0.1:8738'])
    def context(zone='America/Chicago'):
        c=browser.new_context(ignore_https_errors=True,user_agent=UA,timezone_id=zone)
        c.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>false})")
        c.add_init_script(CAPTURE)
        if OLD:
            source=subprocess.check_output(['git','show','2c8aa9a:src/static/assets/ga.js'],cwd=ROOT,text=True)
            c.route('**/assets/ga.js',lambda route:route.fulfill(body=source,content_type='application/javascript'))
        return c
    c=context('Europe/Berlin'); pg=c.new_page(); req=[];pg.on('request',lambda r:req.append(r.url))
    pg.goto('https://invoicesnap.work/',wait_until='networkidle');pg.wait_for_timeout(1000)
    assert not any('google' in u or 'posthog' in u for u in req), 'EU tracker before Accept'
    pg.locator('[data-consent=denied]').click();pg.wait_for_timeout(500)
    assert not any('google' in u or 'posthog' in u for u in req), 'EU tracker after Decline'
    c.close();print('PASS EU no requests before Accept/after Decline',flush=True)
    c=context();pg=c.new_page();req=[];pg.on('request',lambda r:req.append((r.url,r.post_data_buffer)))
    pg.goto('https://invoicesnap.work/invoice-generator/?email='+SECRET+'&token_hash='+SECRET,wait_until='networkidle')
    pg.wait_for_function('window.posthog && posthog.__loaded')
    pg.locator('[data-f]').first.fill(SECRET)
    pg.mouse.click(5,5);pg.evaluate('window.scrollTo(0, document.documentElement.scrollHeight)');pg.wait_for_timeout(8000)
    pg.evaluate("posthog.capture('privacy_regression_control')");pg.wait_for_timeout(6000)
    bodies=[decoded(base64.b64decode(x)) for x in pg.evaluate('window.__bodies')]
    text='\n'.join(bodies)
    assert '$pageview' in text and '$snapshot' in text, 'No real PostHog replay/event payload control'
    assert SECRET not in text, 'Private marker in decoded PostHog payload'
    google='\n'.join(u+decoded(b or b'') for u,b in req if '/g/collect' in u)
    assert 'page_view' in google, 'No GA page_view delivery control'
    assert 'form_start' not in google, 'Unsafe enhanced form interactions enabled on shared GA stream'
    assert SECRET not in google, 'Private marker in GA URL/payload'
    print('PASS real GA/PostHog events and replay have no private URL/form marker',flush=True)
    pg.locator('[data-cookie-settings]').first.click();pg.locator('[data-consent=denied]').click()
    assert pg.evaluate('posthog.has_opted_out_capturing()'), 'Decline ignored'
    pg.locator('[data-cookie-settings]').first.click();pg.locator('[data-consent=granted]').click()
    assert not pg.evaluate('posthog.has_opted_out_capturing()'), 'Regrant leaves PostHog opted out'
    pg.locator('[data-cookie-settings]').first.click();pg.locator('[data-consent=denied]').click()
    pg.reload(wait_until='networkidle');pg.locator('[data-cookie-settings]').first.click();pg.locator('[data-consent=granted]').click()
    pg.wait_for_function('posthog.__loaded');assert not pg.evaluate('posthog.has_opted_out_capturing()'), 'Persisted SDK optout survives Accept'
    c.close();print('PASS US decline/regrant and persisted optout recovery',flush=True)
    c=context('Europe/Berlin');pg=c.new_page();pg.route('https://us-assets.i.posthog.com/static/array.js',lambda r:r.continue_())
    pg.goto('https://invoicesnap.work/',wait_until='networkidle')
    pg.evaluate("document.querySelector('[data-consent=granted]').click();document.querySelector('[data-cookie-settings]').click();document.querySelector('[data-consent=denied]').click()")
    pg.wait_for_timeout(3000);pg.locator('[data-cookie-settings]').first.click();pg.locator('[data-consent=granted]').click()
    pg.wait_for_function('window.posthog && posthog.__loaded');assert not pg.evaluate('posthog.has_opted_out_capturing()')
    c.close();print('PASS Accept/Decline during script load then Accept recovery',flush=True)
    browser.close()
