#!/usr/bin/env python3
"""
CF Proxy v2 — Cloudflare bypass + Captcha detect
=================================================
ranobes_scraper.py bilan birgalikda ishlatiladi.

API:
  GET  /health          → {"status":"ok","requests":N}
  POST /fetch           → {"url":"...","referer":"..."} → {"html":"...","status":200}
                          captcha bo'lsa: {"captcha":true,"captcha_url":"..."}
                          antiflood bo'lsa: auto-kutib qayta urinadi
  POST /close_session   → yangi browser context
  POST /manual_solve    → {"url":"..."} → ko'rinadigan brauzer ochildi, user hal qiladi

Ishga tushirish:
  python cf_proxy.py   yoki   run_proxy.bat
Manzil: http://localhost:8191
"""

import sys, os, json, time, random, threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# ── AUTO-INSTALL ──────────────────────────────────────────────────────────────
def ensure_deps():
    try: __import__('playwright')
    except ImportError:
        import subprocess
        print('[CF Proxy] playwright o\'rnatilmoqda...')
        subprocess.check_call([sys.executable, '-m', 'pip', 'install',
                               '--quiet', '--break-system-packages', 'playwright'])
        subprocess.check_call([sys.executable, '-m', 'playwright', 'install', 'chromium'])

ensure_deps()
from playwright.sync_api import sync_playwright

# ── CONFIG ────────────────────────────────────────────────────────────────────
PORT         = 8191
BASE         = 'https://ranobes.net'
HEADLESS     = True
PAGE_TIMEOUT = 45_000
NAV_TIMEOUT  = 45_000

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0',
]

# Detection keywords
CAPTCHA_SIGNS   = ['hcaptcha', 'recaptcha', "i'm not a robot", 'are you a robot',
                   'verify you are human', 'i am not a robot', 'ddos-guard',
                   'antiflood', 'flood protection']
ANTIFLOOD_SIGNS = ['antiflood', 'too many requests', 'flood', 'rate limit', 'slow down']
CF_SIGNS        = ['cloudflare', 'just a moment', 'checking your browser', 'please wait...']

ANTI_BOT_JS = """
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    Object.defineProperty(navigator, 'plugins',   {get: () => [1,2,3,4,5]});
    Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
    window.chrome = {runtime:{}, loadTimes:()=>{}, csi:()=>{}};
    const _pq = window.navigator.permissions.query.bind(window.navigator.permissions);
    window.navigator.permissions.query = p =>
        p.name === 'notifications'
            ? Promise.resolve({state: Notification.permission})
            : _pq(p);
"""

# ── BROWSER STATE ─────────────────────────────────────────────────────────────
_lock      = threading.Lock()
_pw        = None
_browser   = None
_context   = None
_req_count = 0

def _start_browser():
    global _pw, _browser, _context
    print('[CF Proxy] Chromium ishga tushirilmoqda...')
    _pw = sync_playwright().start()
    _browser = _pw.chromium.launch(
        headless=HEADLESS,
        args=[
            '--no-sandbox',
            '--disable-blink-features=AutomationControlled',
            '--disable-dev-shm-usage',
            '--no-first-run',
            '--disable-gpu',
        ]
    )
    _make_context()
    print('[CF Proxy] Chromium tayyor.')

def _make_context():
    global _context
    if _context:
        try: _context.close()
        except: pass
    _context = _browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        viewport={'width': random.choice([1366, 1440, 1920]),
                  'height': random.choice([768, 900, 1080])},
        locale='en-US',
        extra_http_headers={
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'DNT': '1',
        },
        java_script_enabled=True,
    )
    _context.add_init_script(ANTI_BOT_JS)
    print('[CF Proxy] Yangi browser context ochildi.')

def _check_page(html: str, title: str, status: int) -> tuple:
    """Returns (is_captcha, is_antiflood, is_cf_challenge)"""
    h = html[:7000].lower()
    t = title.lower()
    is_captcha   = any(k in h for k in CAPTCHA_SIGNS)
    is_antiflood = (status == 429) or any(k in h for k in ANTIFLOOD_SIGNS)
    is_cf        = any(k in t for k in CF_SIGNS) or (status == 503 and 'cloudflare' in h)
    return is_captcha, is_antiflood, is_cf

# ── FETCH ─────────────────────────────────────────────────────────────────────
def fetch_url(url: str, referer: str = '') -> dict:
    """
    URL ni real Chromium orqali yuklaydi.
    - CF challenge: avtomatik kutib o'tadi
    - Antiflood: 3 marta qayta urinadi (45/90/180 sek kutib)
    - Captcha: {"captcha": true} qaytaradi
    """
    global _req_count, _context

    with _lock:
        if _browser is None:
            _start_browser()

        _req_count += 1
        # Har 60 so'rovdan keyin context yangilash
        if _req_count > 1 and _req_count % 60 == 0:
            print(f'[CF Proxy] {_req_count} so\'rov — context yangilanmoqda...')
            _make_context()

        antiflood_waits = [45, 90, 180]

        for attempt in range(3):
            page = None
            try:
                page = _context.new_page()
                page.set_default_timeout(PAGE_TIMEOUT)
                page.set_default_navigation_timeout(NAV_TIMEOUT)

                if referer:
                    page.set_extra_http_headers({'Referer': referer})

                resp   = page.goto(url, wait_until='domcontentloaded')
                status = resp.status if resp else 0

                # CF challenge kutish (max 20 sek)
                for _ in range(20):
                    title = page.title().lower()
                    if any(k in title for k in CF_SIGNS):
                        time.sleep(1)
                    else:
                        break

                time.sleep(random.uniform(0.6, 1.3))
                html  = page.content()
                title = page.title()

                is_captcha, is_antiflood, is_cf = _check_page(html, title, status)

                # Still CF challenge after waiting — retry
                if is_cf and not is_captcha and attempt < 2:
                    print(f'[CF Proxy] CF hal qilinmadi, 10s (urinish {attempt+1}/3)')
                    page.close(); page = None
                    time.sleep(10)
                    continue

                # Captcha — faqat odam hal qila oladi
                if is_captcha:
                    captcha_url = page.url
                    print(f'[CF Proxy] CAPTCHA aniqlandi: {captcha_url}')
                    return {'html': html, 'status': status,
                            'captcha': True, 'captcha_url': captcha_url}

                # Antiflood — kutib qayta urinish
                if is_antiflood and attempt < 2:
                    wait = antiflood_waits[attempt]
                    print(f'[CF Proxy] Antiflood! {wait}s kutilmoqda (urinish {attempt+1}/3)...')
                    page.close(); page = None
                    time.sleep(wait)
                    continue

                if is_antiflood:
                    print('[CF Proxy] Antiflood 3 marta ham kechdi.')
                    return {'html': html, 'status': 429, 'antiflood': True}

                return {'html': html, 'status': status, 'url': page.url}

            except Exception as e:
                print(f'[CF Proxy] Xato (urinish {attempt+1}/3): {e}')
                if attempt < 2:
                    time.sleep(5)
            finally:
                if page:
                    try: page.close()
                    except: pass

        return {'html': '', 'status': 0, 'error': 'max_attempts_reached'}

# ── MANUAL CAPTCHA SOLVE ──────────────────────────────────────────────────────
def manual_solve_captcha(url: str) -> dict:
    """
    Ko'rinadigan Chromium oynasini ochadi, user captchani hal qiladi.
    Hal qilingan cookieler asosiy context ga ko'chiriladi.
    """
    print(f'[CF Proxy] Manual captcha hal qilish: {url}')
    vis_pw  = None
    vis_br  = None
    vis_ctx = None
    page    = None

    try:
        # Asosiy contextdan cookieler olish
        with _lock:
            cookies = list(_context.cookies()) if _context else []

        vis_pw  = sync_playwright().start()
        vis_br  = vis_pw.chromium.launch(
            headless=False,
            args=['--no-sandbox', '--disable-blink-features=AutomationControlled',
                  '--window-size=1280,800']
        )
        vis_ctx = vis_br.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={'width': 1280, 'height': 800},
        )
        vis_ctx.add_init_script(ANTI_BOT_JS)

        if cookies:
            try: vis_ctx.add_cookies(cookies)
            except: pass

        page = vis_ctx.new_page()
        page.goto(url, wait_until='domcontentloaded', timeout=30_000)

        print('[CF Proxy] Brauzer ochildi. Captchani hal qiling...')
        print('[CF Proxy] Hal qilgandan so\'ng scraper dasturida "Hal qildim" tugmasini bosing.')

        # Max 5 daqiqa kutish — captcha hal qilinishini polling qilamiz
        start = time.time()
        solved = False
        while time.time() - start < 300:
            try:
                title = page.title().lower()
                cur_u = page.url
                # Agar captcha belgisi yo'q bo'lsa — hal qilingan
                if ('ranobes' in cur_u and
                        not any(k in title for k in
                                ['captcha','robot','verify','checking','antiflood','flood'])):
                    solved = True
                    break
            except:
                pass
            time.sleep(2)

        if solved:
            # Cookielerni asosiy context ga ko'chirish
            solved_cookies = vis_ctx.cookies()
            with _lock:
                if _context:
                    try: _context.add_cookies(solved_cookies)
                    except: pass
            print('[CF Proxy] Captcha hal qilindi! Cookieler ko\'chirildi.')
            return {'status': 'solved'}
        else:
            print('[CF Proxy] Timeout — captcha hal qilinmadi.')
            return {'status': 'timeout'}

    except Exception as e:
        print(f'[CF Proxy] manual_solve xato: {e}')
        return {'status': 'error', 'error': str(e)}
    finally:
        for obj in [page, vis_ctx, vis_br]:
            if obj:
                try: obj.close()
                except: pass
        if vis_pw:
            try: vis_pw.stop()
            except: pass

# ── HTTP SERVER ───────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        print(f'[CF Proxy] {self.address_string()} {fmt % args}')

    def _json(self, code: int, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == '/health':
            self._json(200, {'status': 'ok', 'requests': _req_count})
        else:
            self._json(404, {'error': 'not found'})

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        try:
            body = json.loads(self.rfile.read(length))
        except:
            self._json(400, {'error': 'invalid json'}); return

        if self.path == '/fetch':
            url     = body.get('url', '')
            referer = body.get('referer', BASE + '/')
            if not url:
                self._json(400, {'error': 'url required'}); return
            self._json(200, fetch_url(url, referer))

        elif self.path == '/close_session':
            with _lock:
                _make_context()
            self._json(200, {'status': 'session_reset'})

        elif self.path == '/manual_solve':
            url = body.get('url', BASE + '/')
            # Background thread — HTTP connection stays open until solved (max 5 min)
            result = [None]
            def _run(): result[0] = manual_solve_captcha(url)
            t = threading.Thread(target=_run, daemon=True)
            t.start()
            t.join(timeout=320)
            self._json(200, result[0] or {'status': 'timeout'})

        else:
            self._json(404, {'error': 'not found'})

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    _start_browser()

    print(f'[CF Proxy] ranobes.net ga warmup...')
    warmup = fetch_url(BASE + '/')
    if warmup.get('html'):
        print('[CF Proxy] Warmup muvaffaqiyatli.')
    else:
        print(f'[CF Proxy] Warmup xato: {warmup.get("error","?")} — davom etamiz.')

    server = HTTPServer(('localhost', PORT), Handler)
    print(f'\n{"="*54}')
    print(f'  CF Proxy v2 — http://localhost:{PORT}')
    print(f'  /fetch        → sahifa yuklash (captcha detect)')
    print(f'  /manual_solve → ko\'rinadigan brauzer ochar')
    print(f'  /close_session→ yangi session')
    print(f'  To\'xtatish: Ctrl+C')
    print(f'{"="*54}\n')

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n[CF Proxy] To\'xtatildi.')
    finally:
        if _browser:
            try: _browser.close()
            except: pass
        if _pw:
            try: _pw.stop()
            except: pass

if __name__ == '__main__':
    main()
