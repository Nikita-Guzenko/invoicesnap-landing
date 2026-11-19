"""Exercise the real browser boundary for option 5 without third-party network access."""
import glob,http.server,json,socketserver,threading
from pathlib import Path
from functools import partial
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[3]
class Handler(http.server.SimpleHTTPRequestHandler):
 def translate_path(self,path):
  if path.startswith('/assets/'):return str(ROOT/'src/static'/path.lstrip('/'))
  return super().translate_path(path)
 def log_message(self,*args):pass
server=socketserver.TCPServer(('127.0.0.1',0),partial(Handler,directory=str(ROOT)))
threading.Thread(target=server.serve_forever,daemon=True).start()
with sync_playwright() as p:
 b=p.chromium.launch(executable_path=glob.glob(str(Path.home()/'.cache/ms-playwright/chromium-1200/chrome-linux*/chrome'))[0])
 for width in [1440,390,320]:
  page=b.new_page(viewport={'width':width,'height':900});errors=[];external=[]
  page.on('pageerror',lambda e:errors.append(str(e)))
  page.on('request',lambda r:external.append(r.url) if not r.url.startswith(('http://127.0.0.1:','data:')) else None)
  page.goto(f'http://127.0.0.1:{server.server_address[1]}/previews/nik-696/option-5/',wait_until='networkidle')
  page.wait_for_timeout(700)
  assert page.evaluate('document.documentElement.scrollWidth === innerWidth')
  assert page.evaluate('[...document.images].every(i=>i.complete && i.naturalWidth>0)')
  for idx in range(10):
   page.locator('.gallery-tab').nth(idx).click();page.wait_for_timeout(180)
   assert page.locator('#shotIndex').inner_text().startswith(str(idx+1).zfill(2))
   assert page.locator('#shotTitle').inner_text()==page.evaluate(f'SHOTS[{idx}].title')
   assert page.locator('#shotImg').get_attribute('src')==page.evaluate(f'SHOTS[{idx}].b64')
  start=page.locator('#calcTotal').inner_text()
  page.locator('.preset-btn').nth(1).click()
  assert page.locator('#calcTotal').inner_text()!=start
  page.locator('#currencySelect').select_option('GBP')
  assert page.locator('#calcTotal').inner_text().startswith('£')
  page.locator('#taxSelect').select_option('0')
  assert page.locator('#calcTax').inner_text()=='£0.00'
  page.locator('.qty-btn').nth(1).click()
  assert page.locator('.qty-val').first.inner_text()=='2'
  page.locator('#preview-pdf-btn').click()
  assert page.locator('#invoiceModal').is_visible()
  assert 'Total Due:' in page.locator('#modalItemsList').inner_text()
  page.locator('.modal-close').click();assert not page.locator('#invoiceModal').is_visible()
  page.locator('.faq-q').nth(1).click();assert page.locator('.faq-a').nth(1).is_visible()
  page.locator('.faq-q').nth(1).click();assert not page.locator('.faq-a').nth(1).is_visible()
  assert not errors and not external,(errors,external)
  print(json.dumps({'width':width,'screens':10,'images':'loaded','calculator':'pass','modal':'pass','FAQ':'pass','overflow':False,'externalRequests':external,'errors':errors}))
  page.close()
 b.close()
server.shutdown()
