"""Exercise the real preview HTTP boundary without contacting production (19 Nov 2026)."""
import http.server,socketserver,threading,glob,json
from pathlib import Path
from functools import partial
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[3]
class Handler(http.server.SimpleHTTPRequestHandler):
 def translate_path(self,path):
  if path.startswith('/assets/'):return str(ROOT/'src/static'/path.lstrip('/'))
  return super().translate_path(path)
 def log_message(self,*args):pass
srv=socketserver.TCPServer(('127.0.0.1',0),partial(Handler,directory=str(ROOT)))
threading.Thread(target=srv.serve_forever,daemon=True).start()
url=f'http://127.0.0.1:{srv.server_address[1]}/previews/nik-696/option-2/'
with sync_playwright() as p:
 browser=p.chromium.launch(executable_path=glob.glob(str(Path.home()/'.cache/ms-playwright/chromium-1200/chrome-linux*/chrome'))[0])
 for w in [320,390,768,1440]:
  page=browser.new_page(viewport={'width':w,'height':900});errors=[];external=[]
  page.on('pageerror',lambda e:errors.append(str(e)))
  page.on('request',lambda req:external.append(req.url) if not req.url.startswith(url.split('/previews')[0]) else None)
  page.goto(url,wait_until='networkidle')
  assert page.evaluate('document.documentElement.scrollWidth===innerWidth'),f'overflow {w}'
  assert page.evaluate('[...document.images].every(i=>i.complete&&i.naturalWidth>0)')
  assert page.locator('meta[name=robots]').get_attribute('content')=='noindex,nofollow'
  for i in range(10):
   page.locator('.gallery-tab').nth(i).click();page.wait_for_timeout(160)
   assert page.locator('#shotIndex').inner_text().startswith(f'{i+1:02d}')
   assert page.locator('#shotImg').evaluate('(i)=>i.complete&&i.naturalWidth>0')
  page.locator('.gallery-tab').nth(0).click()
  initial=page.locator('#calcTotal').inner_text();page.locator('.qty-btn').nth(1).click()
  assert page.locator('#calcTotal').inner_text()!=initial
  page.locator('#currencySelect').select_option('GBP');assert page.locator('#calcTotal').inner_text().startswith('£')
  page.locator('.preset-btn').nth(2).click();assert page.locator('#calcBody tr').count()==3
  page.locator('.faq-q').nth(1).click();assert 'open' in page.locator('.faq-item').nth(1).get_attribute('class')
  page.locator('#preview-pdf-btn').click();assert page.locator('#invoiceModal').is_visible()
  page.locator('.modal-close').click();assert not page.locator('#invoiceModal').is_visible()
  if w<860:
   page.locator('.menu-toggle').click();assert page.locator('.nav-links').is_visible()
  assert not errors,errors
  assert not external,external
  print(json.dumps({'width':w,'overflow':False,'tabs':10,'images':'loaded','calculator':'pass','faq':'pass','modal':'pass','external_requests':external,'errors':errors}))
  page.close()
 browser.close()
srv.shutdown()
