#!/usr/bin/env python3
"""NIK-696 independent real-browser checks. Run from repository root.

Uses a loopback server only. Fails for external requests, missing live copy,
images, sections, inert controls, wrong viewport or overflow. Screenshot stitching
is optional with --shots; it avoids unpainted offscreen image layers (19 Nov 2026).
"""
import glob, http.server, socketserver, threading, re, json, sys, io, time
from functools import partial
from pathlib import Path
from playwright.sync_api import sync_playwright
from PIL import Image, ImageChops, ImageStat
ROOT=Path(__file__).resolve().parents[2]; D=Path(__file__).resolve().parent
class Handler(http.server.SimpleHTTPRequestHandler):
 def translate_path(self,path):
  p=path.split('?')[0]
  if p.startswith('/assets/'): return str(ROOT/'src/static'/p.lstrip('/'))
  return super().translate_path(path)
 def log_message(self,*a): pass
srv=socketserver.TCPServer(('127.0.0.1',0),partial(Handler,directory=str(ROOT)))
threading.Thread(target=srv.serve_forever,daemon=True).start(); origin=f'http://127.0.0.1:{srv.server_address[1]}'
exe=glob.glob(str(Path.home()/'.cache/ms-playwright/chromium-1200/chrome-linux*/chrome'))[0]
report=[]
with sync_playwright() as p:
 b=p.chromium.launch(executable_path=exe)
 base=b.new_page(); base.route('**/*',lambda route:route.continue_() if route.request.url.startswith(origin) or route.request.url.startswith('data:') else route.abort())
 base.goto(origin+'/src/pages/index.html'); base.wait_for_timeout(300)
 # Read content leaves, not styling wrapper text, to allow legitimate line wrapping.
 leaves=base.locator('main h2, main h3, main p, main li, main th, main td, .faq-q, .faq-a, footer a').all_text_contents()
 leaves=[' '.join(x.split()) for x in leaves if len(' '.join(x.split()))>10]
 links=base.locator('a[href^="https://apps.apple.com"],a[href^="https://app.invoicesnap.work"]').evaluate_all('(es)=>[...new Set(es.map(e=>e.getAttribute("href")))]')
 for n in [1]:
  for w in [1440,768,390,320]:
   page=b.new_page(viewport={'width':w,'height':900 if w>400 else 844},device_scale_factor=1,reduced_motion='reduce')
   errors=[]; external=[]
   page.on('pageerror',lambda e:errors.append(str(e)))
   page.on('request',lambda r:external.append(r.url) if not r.url.startswith(origin) and not r.url.startswith('data:') else None)
   page.goto(f'{origin}/previews/nik-696/option-{n}/',wait_until='networkidle')
   assert page.evaluate('innerWidth')==w
   assert page.locator('meta[name=robots]').get_attribute('content')=='noindex,nofollow'
   assert page.locator('script[src], script[type="application/ld+json"]').count()==0
   for section in ['showcase','compare','trades','calculator','pricing','faq']: assert page.locator('#'+section).count(),(n,'missing section',section)
   for y in range(0,page.evaluate('document.documentElement.scrollHeight'),500): page.evaluate('(y)=>scrollTo(0,y)',y);page.wait_for_timeout(40)
   assert page.evaluate('[...document.images].every(i=>i.complete&&i.naturalWidth>0)'),(n,w,'broken image')
   assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(n,w,'overflow')
   txt=re.sub(r'\s+','',page.locator('body').text_content())
   missing=[x for x in leaves if re.sub(r'\s+','',x) not in txt]
   # Hidden gallery descriptions are checked by selecting their ten tabs below.
   assert not missing,(n,w,'missing copy',missing)
   for href in links: assert page.locator('a').evaluate_all('(es,h)=>es.some(e=>e.getAttribute("href")===h)',href),(n,'missing href',href)
   tabs=page.locator('.gallery-tab')
   if tabs.count()==10:
    for i in range(10):
     tabs.nth(i).click();page.wait_for_timeout(160)
     assert tabs.nth(i).evaluate('(e)=>e.classList.contains("active")'),(n,w,'tab',i)
     assert page.locator('#shotImg').evaluate('(e)=>e.complete&&e.naturalWidth>0')
    tabs.nth(0).click();page.wait_for_timeout(160)
   else: raise AssertionError((n,w,'expected ten gallery tabs',tabs.count()))
   before=page.locator('#calcTotal').inner_text();page.locator('.qty-btn').nth(1).click();after=page.locator('#calcTotal').inner_text();assert before!=after,(n,w,'quantity inert')
   page.locator('#currencySelect').select_option('GBP');assert '£' in page.locator('#calcTotal').inner_text()
   page.locator('#currencySelect').select_option('USD')
   faq=page.locator('.faq-q').first;was_open=faq.evaluate('(e)=>e.parentElement.classList.contains("open")');faq.click();assert faq.evaluate('(e)=>e.parentElement.classList.contains("open")') != was_open;faq.click()
   assert not errors,(n,w,errors);assert not external,(n,w,external)
   if '--shots' in sys.argv and w in [1440,390]:
    page.reload(wait_until='networkidle');page.wait_for_timeout(200)
    height=page.evaluate('document.documentElement.scrollHeight');h=page.viewport_size['height']
    for y in range(0,height,500):page.evaluate('(y)=>scrollTo(0,y)',y);page.wait_for_timeout(40)
    page.evaluate('scrollTo(0,0)');page.wait_for_timeout(100)
    full=Image.open(io.BytesIO(page.screenshot(full_page=True))).convert('RGB'); stitched=Image.new('RGB',(w,height))
    for y in range(0,height,h):
     if y: page.locator('header').evaluate_all('(es)=>es.forEach(e=>{if(["sticky","fixed"].includes(getComputedStyle(e).position))e.style.visibility="hidden"})')
     page.evaluate('(y)=>scrollTo(0,y)',y);page.wait_for_timeout(70);actual=page.evaluate('scrollY')
     im=Image.open(io.BytesIO(page.screenshot())).convert('RGB');top=max(0,y-actual);stitched.paste(im.crop((0,top,w,min(h,height-actual))),(0,y))
    stitched.save(D/f'shots/option-{n}-{w}-stitched.png')
    diff=ImageStat.Stat(ImageChops.difference(full,stitched)).mean
    report.append({'option':n,'width':w,'stitch_mean_difference':diff})
   report.append({'option':n,'width':w,'overflow':False,'errors':errors,'external':external,'missing_short_copy':missing})
   page.close()
 b.close()
srv.shutdown();(D/'verification.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
