#!/usr/bin/env python3
"""
Ranobes Scraper v8
==================
v8 yangiliklari:
  - Thread-safe proxy cache (Lock)
  - Atomic progress/cache yozish (crash-safe)
  - get_chapter_num tuzatildi (oxirgi son, birinchi emas)
  - Default output → skript papkasi (System32 bug yo'q)
  - .docx extension majburiy
  - Log cheklovi: max 2000 qator (memory leak yo'q)
  - Log aqlli autoscroll (user scroll qilsa to'xtaydi)
  - "Papkani ochish" tugmasi yuklash tugagach
  - Cloudscraper session yangilanishi (har 80 chapter'da)
  - except bare → except Exception (xatolar ko'rinadi)
  - Oyna minimal o'lchami belgilandi
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import re, time, os, sys, random, json

# ── AUTO-INSTALL ──────────────────────────────────────────────────────────────
def ensure_deps():
    needed = []
    for pkg, imp in [('requests','requests'), ('beautifulsoup4','bs4'),
                     ('lxml','lxml'), ('python-docx','docx'),
                     ('cloudscraper','cloudscraper')]:
        try: __import__(imp)
        except ImportError: needed.append(pkg)
    if needed:
        import subprocess
        subprocess.check_call([sys.executable, '-m', 'pip', 'install',
                               '--quiet', '--break-system-packages'] + needed)

ensure_deps()

import requests
import cloudscraper
from bs4 import BeautifulSoup
from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH

# ── CF PROXY ──────────────────────────────────────────────────────────────────
CF_PROXY_URL  = 'http://localhost:8191'
_proxy_lock   = threading.Lock()
_proxy_cache  = {'ok': False, 'ts': 0.0}
_PROXY_TTL    = 8   # seconds

def check_proxy() -> bool:
    """cf_proxy.py ishlab turganini tekshiradi (thread-safe cache bilan)."""
    with _proxy_lock:
        now = time.time()
        if now - _proxy_cache['ts'] < _PROXY_TTL:
            return _proxy_cache['ok']
    # Lock tashqarida HTTP so'rov — boshqa threadlarni bloklamaydi
    try:
        r = requests.get(CF_PROXY_URL + '/health', timeout=2)
        ok = r.status_code == 200
    except Exception:
        ok = False
    with _proxy_lock:
        _proxy_cache['ok'] = ok
        _proxy_cache['ts'] = time.time()
    return ok

def _invalidate_proxy_cache():
    _proxy_cache['ts'] = 0.0

CAPTCHA_SIGNS = ['hcaptcha', 'recaptcha', "i'm not a robot", 'are you a robot',
                 'verify you are human', 'antiflood', 'ddos-guard',
                 'flood protection', 'too many requests']

def _is_captcha_html(html: str) -> bool:
    h = html[:7000].lower()
    return any(k in h for k in CAPTCHA_SIGNS)

def _proxy_fetch(url: str, referer: str = '') -> tuple:
    """Returns (soup, html, is_captcha)"""
    try:
        r = requests.post(CF_PROXY_URL + '/fetch',
                          json={'url': url, 'referer': referer},
                          timeout=70)
        r.raise_for_status()
        data = r.json()

        # Proxy explicitly flags captcha
        if data.get('captcha') or data.get('antiflood'):
            return None, data.get('html', ''), True

        if data.get('status') == 200 and data.get('html'):
            html = data['html']
            if _is_captcha_html(html):
                return None, html, True
            soup = BeautifulSoup(html, 'lxml')
            return soup, html, False
    except Exception:
        pass
    return None, '', False

def make_session() -> cloudscraper.CloudScraper:
    return cloudscraper.create_scraper(
        browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
    )

# ── CONSTANTS ─────────────────────────────────────────────────────────────────
BASE = 'https://ranobes.net'

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0',
]

def make_headers(referer=None):
    return {
        'User-Agent':              random.choice(USER_AGENTS),
        'Accept':                  'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language':         'en-US,en;q=0.9,ru;q=0.8',
        'Accept-Encoding':         'gzip, deflate, br',
        'Connection':              'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Referer':                 referer or (BASE + '/'),
        'Sec-Fetch-Dest':          'document',
        'Sec-Fetch-Mode':          'navigate',
        'Sec-Fetch-Site':          'same-origin',
        'Sec-Fetch-User':          '?1',
        'Cache-Control':           'max-age=0',
        'DNT':                     '1',
    }

# ── URL UTILS ─────────────────────────────────────────────────────────────────
def normalize_url(url: str) -> str:
    url = url.strip().rstrip('/')
    if not url.startswith('http'): url = 'https://' + url
    if re.search(r'/[a-z0-9\-]+-\d{5,}/\d+$', url): url += '.html'
    return url

def abs_url(href: str) -> str:
    if not href or href.startswith('javascript') or href.startswith('#'): return ''
    if href.startswith('http'): return href
    if href.startswith('//'): return 'https:' + href
    return BASE + ('/' if not href.startswith('/') else '') + href.lstrip('/')

def is_chapter_url(url: str) -> bool:
    return bool(url and re.search(r'ranobes\.net/[a-z0-9\-]+-\d{4,}/\d+\.html$', url))

def get_chapter_num(text: str) -> int:
    if not text: return -1
    # 1. Aniq "chapter N" / "ch N" iborasi — eng ishonchli
    m = re.search(r'(?:chapter|ch\.?|chap\.?)\s*#?\s*(\d+)', text, re.I)
    if m: return int(m.group(1))
    # 2. Boshidan boshlanadigan son (sarlavha "123. Title" ko'rinishida)
    m = re.search(r'^(\d+)[.\s\-:]', text.strip())
    if m: return int(m.group(1))
    # 3. Oxirgi son — "Volume 3 Part 12" → 12 (avval birinchi son olinardi → 3)
    all_nums = re.findall(r'\b(\d+)\b', text)
    if all_nums: return int(all_nums[-1])
    return -1

def get_novel_name(url: str) -> str:
    m = re.search(r'ranobes\.net/([^/]+)/', url)
    if not m: return 'novel'
    slug = m.group(1)
    slug = re.sub(r'-v\d+', '', slug)
    slug = re.sub(r'-\d{4,}', '', slug)
    return slug.strip('-').replace('-', ' ').title() or 'novel'

# ── SMART FETCH ───────────────────────────────────────────────────────────────
def smart_fetch(session, url: str, log,
                stop_flag=None, referer=None, retries=4) -> tuple:
    """
    Returns: (soup, html, is_captcha)
    1) CF Proxy (real Chrome) — captcha detect qaytaradi
    2) cloudscraper — antiflood bo'lsa kutadi
    """
    # 1. CF Proxy
    if check_proxy():
        soup, html, captcha = _proxy_fetch(url, referer or BASE + '/')
        if captcha:
            return None, html, True
        if soup:
            return soup, html, False
        log('  cf_proxy xato, cloudscraper ga o\'tildi.')

    # 2. cloudscraper
    wait_times = [45, 90, 150, 240]
    for attempt in range(retries):
        if stop_flag and stop_flag(): return None, '', False
        try:
            session.headers.update(make_headers(referer or BASE + '/'))
            resp = session.get(url, timeout=30, allow_redirects=True)
            html = resp.text

            if resp.status_code == 429 or _is_captcha_html(html):
                wait = wait_times[min(attempt, len(wait_times)-1)]
                log(f'  Antiflood! {wait}s kutilmoqda... ({attempt+1}/{retries})')
                for _ in range(wait):
                    if stop_flag and stop_flag(): return None, '', False
                    time.sleep(1)
                continue

            if resp.status_code == 404:
                log(f'  404: {url}')
                return None, '', False

            resp.raise_for_status()
            soup = BeautifulSoup(html, 'lxml')
            return soup, html, False

        except requests.exceptions.ConnectionError:
            log('  Ulanish xatosi, 10s...')
            time.sleep(10)
        except requests.exceptions.Timeout:
            log('  Timeout, 15s...')
            time.sleep(15)
        except Exception as e:
            log(f'  Xato ({attempt+1}): {e}')
            time.sleep(5)

    return None, '', False

# ── NEXT LINK ─────────────────────────────────────────────────────────────────
def find_next_link(soup: BeautifulSoup, current_url: str) -> str:
    # 1. title="Right button"
    for a in soup.find_all('a', title=True, href=True):
        if 'right' in a['title'].lower():
            u = abs_url(a['href'])
            if is_chapter_url(u) and u != current_url: return u
    # 2. rel="next"
    for a in soup.find_all('a', href=True):
        rel = ' '.join(a.get('rel', []))
        if 'next' in rel.lower():
            u = abs_url(a['href'])
            if is_chapter_url(u) and u != current_url: return u
    # 3. Matn
    for a in soup.find_all('a', href=True):
        txt = a.get_text(strip=True).lower()
        if txt in {'next','next chapter','next »','>','>>','→','»'}:
            u = abs_url(a['href'])
            if is_chapter_url(u) and u != current_url: return u
    # 4. CSS class
    for cls in ['next','next-chapter','nextchap','btn-next','nav-next','r-btn','right-btn']:
        for a in soup.find_all('a', href=True):
            if cls in ' '.join(a.get('class',[])).lower():
                u = abs_url(a['href'])
                if is_chapter_url(u) and u != current_url: return u
    # 5. Next ID
    cur_m = re.search(r'/(\d+)\.html$', current_url)
    if cur_m:
        cur_id   = int(cur_m.group(1))
        cur_slug = re.search(r'ranobes\.net/([^/]+)/', current_url)
        cur_slug = cur_slug.group(1) if cur_slug else ''
        forward  = []
        for a in soup.find_all('a', href=True):
            u = abs_url(a['href'])
            if not is_chapter_url(u) or u == current_url: continue
            um = re.search(r'/(\d+)\.html$', u)
            us = re.search(r'ranobes\.net/([^/]+)/', u)
            if um and us and us.group(1) == cur_slug:
                uid = int(um.group(1))
                if uid > cur_id: forward.append((uid, u))
        if forward:
            forward.sort(); return forward[0][1]
    return ''

def find_chapter_title(soup: BeautifulSoup) -> tuple:
    for sel in ['h1.chapter-title','.chapter-title h1','.reading-title h1',
                'h1','h2.title','.entry-title','h2']:
        el = soup.select_one(sel)
        if el:
            t = el.get_text(strip=True)
            if t and len(t) > 2: return t, get_chapter_num(t)
    return '', -1

# ── CONTENT EXTRACTION ────────────────────────────────────────────────────────
CONTENT_SEL = ['div#arrticle','div.text','div#text','.chapter-content',
               '.reading-content','.entry-content','div[id*="article"]',
               'div[id*="content"]','div[class*="chapter-text"]','article']
JUNK_TAGS   = ['script','style','nav','header','footer','aside',
               'form','iframe','button','select','noscript']
JUNK_SEL    = ['.adsbygoogle','.ad','.advertisement','.social-share',
               '.nav-links','.chapter-nav','.navigation','.donate','.comments']
NAV_RE      = re.compile(
    r'^(previous|next|chapter|back|forward|[←→]|\d+|\[.*?\]|prev|[«»]|следующая|назад)$', re.I)

def extract_content(soup: BeautifulSoup) -> list:
    for tag in JUNK_TAGS:
        for el in soup.find_all(tag): el.decompose()
    for sel in JUNK_SEL:
        for el in soup.select(sel): el.decompose()
    content = None
    for sel in CONTENT_SEL:
        c = soup.select_one(sel)
        if c and len(c.get_text(strip=True)) > 100:
            content = c; break
    if not content:
        divs = [(len(d.get_text()), d) for d in soup.find_all('div')
                if len(d.get_text(strip=True)) > 100]
        if divs: content = max(divs, key=lambda x: x[0])[1]
    if not content: return []
    for a in content.select('a'):
        if NAV_RE.match(a.get_text(strip=True)): a.decompose()
    paragraphs = []
    ps = content.find_all('p')
    if ps:
        for p in ps:
            text = re.sub(r'\s+', ' ', p.get_text(' ', strip=True)).strip()
            if len(text) > 5 and not NAV_RE.match(text): paragraphs.append(text)
    else:
        for line in content.get_text('\n').split('\n'):
            text = re.sub(r'\s+', ' ', line).strip()
            if len(text) > 5 and not NAV_RE.match(text): paragraphs.append(text)
    return paragraphs

# ── SCAN ──────────────────────────────────────────────────────────────────────
def scan_chapters(start_url: str, session, log, stop_flag,
                  progress_cb=None, max_ch=5000) -> list:
    chapters  = []
    visited   = set()
    current   = normalize_url(start_url)
    req_count = 0
    has_proxy = check_proxy()

    log(f'Skan: {current}')
    log('(Bekor qilish uchun "Bekor" ni bosing)\n')

    while current and len(chapters) < max_ch:
        if stop_flag(): break
        if current in visited: break
        visited.add(current)
        req_count += 1

        # Dam olish
        if has_proxy:
            if req_count > 1 and req_count % 80 == 0:
                log(f'  [Dam] {req_count} so\'rov — 5s...')
                for _ in range(5):
                    if stop_flag(): return chapters
                    time.sleep(1)
        else:
            if req_count > 1 and req_count % 25 == 0:
                log(f'  [Dam] {req_count} so\'rov — 15s...')
                for _ in range(15):
                    if stop_flag(): return chapters
                    time.sleep(1)

        referer = chapters[-1]['url'] if chapters else BASE + '/'
        soup, _, captcha = smart_fetch(session, current, log,
                                       stop_flag=stop_flag, referer=referer)

        if captcha:
            log('  ⚠️  Captcha aniqlandi skan paytida. Skan to\'xtatildi.')
            log('  cf_proxy.py brauzerda captchani hal qiling, keyin qayta skan bajaring.')
            break

        if soup is None:
            log('  Yuklab bo\'lmadi, skan to\'xtatilmoqda.')
            break

        title, num = find_chapter_title(soup)
        next_url   = find_next_link(soup, current)

        chapters.append({
            'url':   current,
            'title': title or f'Chapter {len(chapters)+1}',
            'num':   num if num >= 0 else len(chapters) + 1,
        })

        if progress_cb:
            progress_cb(len(chapters), title, bool(next_url))
        if len(chapters) % 10 == 0:
            log(f'  {len(chapters)} ta topildi — {title}')
        if not next_url:
            log('  Next link topilmadi. Skan tugadi.')
            break

        current = next_url
        time.sleep(random.uniform(0.5, 1.0) if has_proxy else random.uniform(1.2, 2.8))

    return chapters

# ── DOCX BUILDER ──────────────────────────────────────────────────────────────
def build_docx(novel_title: str, chapters_data: list, out_path: str):
    doc = Document()
    sec = doc.sections[0]
    sec.left_margin = sec.right_margin = Inches(1.18)
    sec.top_margin  = sec.bottom_margin = Inches(1)
    doc.styles['Normal'].font.name = 'Times New Roman'
    doc.styles['Normal'].font.size = Pt(12)

    if novel_title:
        p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = p.add_run(novel_title)
        r.bold = True; r.font.size = Pt(22); r.font.name = 'Times New Roman'
        doc.add_paragraph(); doc.add_page_break()

    for idx, (ch_title, paras) in enumerate(chapters_data):
        h = doc.add_heading(level=1); h.clear()
        run = h.add_run(ch_title)
        run.font.name = 'Times New Roman'; run.font.size = Pt(14)
        run.bold = True; run.font.color.rgb = RGBColor(0x1A, 0x1A, 0x1A)
        h.paragraph_format.space_before = Pt(0)
        h.paragraph_format.space_after  = Pt(12)
        if not paras:
            doc.add_paragraph('[Chapter yuklanmadi yoki bosh]')
        else:
            for text in paras:
                p = doc.add_paragraph()
                p.add_run(text).font.name = 'Times New Roman'
                p.paragraph_format.first_line_indent = Pt(24)
                p.paragraph_format.space_after  = Pt(0)
                p.paragraph_format.space_before = Pt(0)
                p.paragraph_format.line_spacing = Pt(18)
        if idx < len(chapters_data) - 1:
            doc.add_page_break()
    doc.save(out_path)

def _atomic_json_write(path: str, data: dict):
    """JSON ni avvol .tmp faylga yozib, keyin rename qiladi — crash-safe."""
    tmp = path + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)   # atomic on all platforms
    except Exception:
        try: os.remove(tmp)
        except Exception: pass

# ── CACHE ─────────────────────────────────────────────────────────────────────
def _cache_path(start_url: str) -> str:
    slug = re.search(r'ranobes\.net/([^/]+)/', start_url)
    slug = slug.group(1) if slug else 'novel'
    d = os.path.dirname(os.path.abspath(sys.argv[0]))
    return os.path.join(d, f'.cache_{slug}.json')

def save_cache(chapters, start_url):
    _atomic_json_write(_cache_path(start_url),
                       {'start': start_url, 'chapters': chapters})

def load_cache(start_url) -> list:
    try:
        p = _cache_path(start_url)
        if not os.path.exists(p): return []
        with open(p, encoding='utf-8') as f:
            data = json.load(f)
        if data.get('start') == start_url: return data.get('chapters', [])
    except Exception: pass
    return []

def clear_cache(start_url):
    try:
        p = _cache_path(start_url)
        if os.path.exists(p): os.remove(p)
    except Exception: pass

# ── PROGRESS ──────────────────────────────────────────────────────────────────
def _progress_path(out_path: str) -> str:
    return os.path.splitext(out_path)[0] + '.progress.json'

def save_progress(out_path, collected, failed, from_num, to_num, target_nums):
    data = {'from_num': from_num, 'to_num': to_num, 'target_nums': target_nums,
            'failed': failed,
            'collected': [{'title': t, 'paras': p} for t, p in collected]}
    _atomic_json_write(_progress_path(out_path), data)

def load_progress(out_path):
    p = _progress_path(out_path)
    if not os.path.exists(p): return None
    try:
        with open(p, encoding='utf-8') as f: return json.load(f)
    except Exception: return None

def clear_progress(out_path):
    try:
        p = _progress_path(out_path)
        if os.path.exists(p): os.remove(p)
    except Exception: pass

# ── SCRIPT DIR ────────────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))

# ── GUI ───────────────────────────────────────────────────────────────────────
class App:
    # ── Palette ──
    BG    = '#0d0d1b'
    CARD  = '#14142a'
    CARD2 = '#1c1c35'
    ACC   = '#7c6af5'   # purple
    ACC2  = '#00c9a7'   # teal
    FG    = '#e4e2ff'
    GRAY  = '#555570'
    RED   = '#ff4b5c'
    GRN   = '#2ecc71'
    WARN  = '#f0a500'
    CAPBG = '#1e0a0a'

    LOG_MAX_LINES = 2000   # xotira to'lib ketmasligi uchun

    def __init__(self, root: tk.Tk):
        self.root           = root
        self.root.title('Ranobes Scraper v8')
        self.root.geometry('840x780')
        self.root.minsize(700, 600)           # oyna juda kichik bo'lib qolmasin
        self.root.resizable(True, True)
        self.root.configure(bg=self.BG)
        self._stop             = False
        self._chapters         = []
        self._start_url        = ''
        self._captcha_waiting  = False
        self._captcha_url      = ''
        self._dl_start_time    = 0.0
        self._dl_done_count    = 0
        self._dl_total         = 0
        self._build_ui()
        self._start_proxy_watcher()

    # ─────────────────────────────────────── BUILD UI ─────────────────────────
    def _build_ui(self):
        BG=self.BG; CARD=self.CARD; C2=self.CARD2
        ACC=self.ACC; FG=self.FG; GR=self.GRAY

        s = ttk.Style(); s.theme_use('clam')
        s.configure('TFrame',        background=BG)
        s.configure('TLabel',        background=BG, foreground=FG, font=('Segoe UI',10))
        s.configure('TEntry',        fieldbackground=C2, foreground=FG,
                    insertcolor=FG, borderwidth=0)
        s.configure('Acc.TButton',   background=ACC, foreground='white',
                    font=('Segoe UI',10,'bold'), borderwidth=0, padding=(10,6))
        s.map('Acc.TButton',  background=[('active','#9484ff'),('disabled','#33335a')])
        s.configure('Stop.TButton',  background=self.RED, foreground='white',
                    font=('Segoe UI',10,'bold'), borderwidth=0, padding=(10,6))
        s.map('Stop.TButton', background=[('active','#ff6b7a'),('disabled','#552233')])
        s.configure('Warn.TButton',  background=self.WARN, foreground='white',
                    font=('Segoe UI',9,'bold'), borderwidth=0, padding=(8,5))
        s.map('Warn.TButton', background=[('active','#f5c642')])
        s.configure('Sml.TButton',   background=C2, foreground=FG,
                    font=('Segoe UI',9), borderwidth=0, padding=(6,4))
        s.map('Sml.TButton',  background=[('active','#28284a'),('disabled','#222233')])
        s.configure('Prg.TProgressbar', troughcolor=C2, background=ACC, thickness=7)
        s.layout('Prg.TProgressbar', s.layout('Horizontal.TProgressbar'))

        outer = ttk.Frame(self.root, padding=(22, 18, 22, 14))
        outer.pack(fill=tk.BOTH, expand=True)

        # ── HEADER ──────────────────────────────────────────────────────────
        hdr = ttk.Frame(outer); hdr.pack(fill=tk.X, pady=(0, 14))

        title_f = ttk.Frame(hdr); title_f.pack(side=tk.LEFT)
        tk.Label(title_f, text='Ranobes Scraper', bg=BG, fg=FG,
                 font=('Segoe UI', 17, 'bold')).pack(side=tk.LEFT)
        tk.Label(title_f, text=' v7', bg=ACC, fg='white',
                 font=('Segoe UI', 9, 'bold'), padx=6, pady=3).pack(
                     side=tk.LEFT, padx=(6,0), pady=4)

        # Proxy status (right)
        prx = ttk.Frame(hdr); prx.pack(side=tk.RIGHT, anchor=tk.E)
        self.proxy_dot = tk.Label(prx, text='●', bg=BG, fg=GR, font=('Segoe UI',13))
        self.proxy_dot.pack(side=tk.LEFT)
        self.proxy_txt = tk.StringVar(value='CF Proxy tekshirilmoqda...')
        tk.Label(prx, textvariable=self.proxy_txt, bg=BG, fg=GR,
                 font=('Segoe UI', 9)).pack(side=tk.LEFT, padx=(3,0))

        # ── URL CARD ────────────────────────────────────────────────────────
        uc = tk.Frame(outer, bg=CARD, padx=16, pady=13)
        uc.pack(fill=tk.X, pady=(0, 6))

        tk.Label(uc, text='BOSHLANG\'ICH CHAPTER URL', bg=CARD, fg=GR,
                 font=('Segoe UI', 8, 'bold')).pack(anchor=tk.W)

        url_row = tk.Frame(uc, bg=CARD); url_row.pack(fill=tk.X, pady=(7,0))

        # Entry with inner padding
        url_bg = tk.Frame(url_row, bg=C2, padx=8, pady=0)
        url_bg.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0,8))
        self.url_var = tk.StringVar()
        tk.Entry(url_bg, textvariable=self.url_var, bg=C2, fg=FG,
                 insertbackground=FG, font=('Segoe UI', 10),
                 relief=tk.FLAT, bd=0).pack(fill=tk.X, ipady=7)

        self.scan_btn = ttk.Button(url_row, text='⟳  Skan',
                                    style='Acc.TButton', command=self._start_scan, width=9)
        self.scan_btn.pack(side=tk.LEFT, padx=(0,4))
        self.stop_scan_btn = ttk.Button(url_row, text='✕  Bekor',
                                         style='Sml.TButton', command=self._do_stop,
                                         state=tk.DISABLED, width=9)
        self.stop_scan_btn.pack(side=tk.LEFT)

        self.scan_info = tk.Label(outer,
            text='Misol: https://ranobes.net/shadow-slave-v741610-1205249/1806169.html',
            bg=BG, fg='#4a7ab5', font=('Segoe UI',8), wraplength=795, anchor=tk.W)
        self.scan_info.pack(anchor=tk.W, pady=(4, 10))

        # ── RANGE  +  SAVE (two columns) ────────────────────────────────────
        row2 = tk.Frame(outer, bg=BG); row2.pack(fill=tk.X, pady=(0,6))

        # Range card
        rc = tk.Frame(row2, bg=CARD, padx=16, pady=13)
        rc.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0,6))
        tk.Label(rc, text='DIAPAZON', bg=CARD, fg=GR,
                 font=('Segoe UI',8,'bold')).grid(row=0, column=0, columnspan=6, sticky=tk.W)
        tk.Label(rc, text='Dan:',   bg=CARD, fg=FG, font=('Segoe UI',10)).grid(
            row=1, column=0, sticky=tk.W, pady=(7,0))
        self.from_var = tk.StringVar(value='1')
        tk.Entry(rc, textvariable=self.from_var, width=8, bg=C2, fg=FG,
                 insertbackground=FG, font=('Segoe UI',10), relief=tk.FLAT, bd=0
                 ).grid(row=1, column=1, padx=(5,14), pady=(7,0), ipady=5)
        tk.Label(rc, text='Gacha:', bg=CARD, fg=FG, font=('Segoe UI',10)).grid(
            row=1, column=2, sticky=tk.W, pady=(7,0))
        self.to_var = tk.StringVar(value='10')
        tk.Entry(rc, textvariable=self.to_var, width=8, bg=C2, fg=FG,
                 insertbackground=FG, font=('Segoe UI',10), relief=tk.FLAT, bd=0
                 ).grid(row=1, column=3, padx=(5,0), pady=(7,0), ipady=5)
        self.range_lbl = tk.Label(rc, text='(avval Skan bajaring)',
                                   bg=CARD, fg=GR, font=('Segoe UI',8))
        self.range_lbl.grid(row=2, column=0, columnspan=6, sticky=tk.W, pady=(5,0))

        # Save card
        sc = tk.Frame(row2, bg=CARD, padx=16, pady=13)
        sc.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tk.Label(sc, text='SAQLASH (.docx)', bg=CARD, fg=GR,
                 font=('Segoe UI',8,'bold')).pack(anchor=tk.W)
        sf = tk.Frame(sc, bg=CARD); sf.pack(fill=tk.X, pady=(7,0))
        self.out_var = tk.StringVar(value=os.path.join(SCRIPT_DIR, 'novel.docx'))
        tk.Entry(sf, textvariable=self.out_var, bg=C2, fg=FG,
                 insertbackground=FG, font=('Segoe UI',10), relief=tk.FLAT, bd=0
                 ).pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=5)
        ttk.Button(sf, text='…', width=3, style='Sml.TButton',
                   command=self._choose_out).pack(side=tk.LEFT, padx=(7,0))

        # ── CAPTCHA BANNER (hidden initially) ───────────────────────────────
        self.captcha_frame = tk.Frame(outer, bg=self.CAPBG, padx=16, pady=12)
        # NOT packed yet — shown when captcha detected

        tk.Label(self.captcha_frame,
                 text='🔴  CAPTCHA / ANTIFLOOD ANIQLANDI — Yuklash to\'xtatildi',
                 bg=self.CAPBG, fg='#ff7070', font=('Segoe UI',10,'bold')
                 ).pack(anchor=tk.W)

        self.captcha_url_lbl = tk.Label(self.captcha_frame, text='',
                                         bg=self.CAPBG, fg='#888899',
                                         font=('Segoe UI',8), wraplength=790, anchor=tk.W)
        self.captcha_url_lbl.pack(anchor=tk.W, pady=(3,8))

        cbr = tk.Frame(self.captcha_frame, bg=self.CAPBG); cbr.pack(anchor=tk.W)
        self.manual_btn = ttk.Button(cbr, text='🌐  Brauzerda hal qilish',
                                      style='Warn.TButton',
                                      command=self._captcha_manual_solve)
        self.manual_btn.pack(side=tk.LEFT, padx=(0,8))
        ttk.Button(cbr, text='✅  Hal qildim — davom etish',
                   style='Acc.TButton',
                   command=self._captcha_resolved).pack(side=tk.LEFT)

        tk.Label(self.captcha_frame,
                 text='  1. "Brauzerda hal qilish" → Chromium oynasi ochiladi → captchani yeching\n'
                      '  2. Yoki qo\'lda brauzerda ranobes.net ga kirib captchani hal qiling\n'
                      '  3. Hal qilgandan so\'ng "Hal qildim" tugmasini bosing — yuklash davom etadi',
                 bg=self.CAPBG, fg='#666677', font=('Segoe UI',8), justify=tk.LEFT
                 ).pack(anchor=tk.W, pady=(7,0))

        # ── PROGRESS ────────────────────────────────────────────────────────
        self.prog_frame = tk.Frame(outer, bg=BG)
        self.prog_frame.pack(fill=tk.X, pady=(10,0))

        self.prog_var = tk.DoubleVar()
        ttk.Progressbar(self.prog_frame, variable=self.prog_var,
                        maximum=100, style='Prg.TProgressbar').pack(fill=tk.X)

        # Status + ETA row
        str_row = tk.Frame(outer, bg=BG); str_row.pack(fill=tk.X, pady=(5,5))
        self.status_var = tk.StringVar(value='Tayyor')
        tk.Label(str_row, textvariable=self.status_var, bg=BG, fg=GR,
                 font=('Segoe UI',9), anchor=tk.W).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.eta_var = tk.StringVar(value='')
        tk.Label(str_row, textvariable=self.eta_var, bg=BG, fg=self.ACC2,
                 font=('Segoe UI',9,'bold')).pack(side=tk.RIGHT)

        # ── LOG ─────────────────────────────────────────────────────────────
        lf = tk.Frame(outer, bg=CARD, padx=2, pady=2)
        lf.pack(fill=tk.BOTH, expand=True, pady=(6,8))

        self.log_box = tk.Text(lf, bg='#08080f', fg='#9898c0',
                               insertbackground=FG, font=('Consolas',9),
                               wrap=tk.WORD, state=tk.DISABLED,
                               relief=tk.FLAT, padx=10, pady=8, height=9)
        sb = tk.Scrollbar(lf, command=self.log_box.yview,
                          bg=CARD, troughcolor=C2, relief=tk.FLAT, width=8)
        self.log_box.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_box.pack(fill=tk.BOTH, expand=True)

        self.log_box.tag_configure('ok',   foreground='#2ecc71')
        self.log_box.tag_configure('err',  foreground='#ff4b5c')
        self.log_box.tag_configure('warn', foreground='#f0a500')
        self.log_box.tag_configure('info', foreground='#5b9bd5')
        self.log_box.tag_configure('head', foreground=ACC, font=('Consolas',9,'bold'))

        # ── BUTTONS ─────────────────────────────────────────────────────────
        br = tk.Frame(outer, bg=BG); br.pack(fill=tk.X)
        self.start_btn = ttk.Button(br, text='▶  Boshlash',
                                     style='Acc.TButton', command=self._start_download)
        self.start_btn.pack(side=tk.LEFT)
        self.stop_btn = ttk.Button(br, text="■  To'xtatish",
                                    style='Stop.TButton',
                                    command=self._do_stop, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=(8,0))
        ttk.Button(br, text='🗑  Cache', style='Sml.TButton',
                   command=self._del_cache).pack(side=tk.RIGHT)

    # ─────────────────────────────────────── PROXY WATCHER ───────────────────
    def _start_proxy_watcher(self):
        def _watcher():
            while True:
                ok = check_proxy()
                def _upd(ok=ok):
                    color = self.GRN if ok else self.GRAY
                    text  = 'CF Proxy faol ✓' if ok else 'CF Proxy yoq'
                    try:
                        self.proxy_dot.config(fg=color)
                        self.proxy_txt.set(text)
                    except: pass
                try: self.root.after(0, _upd)
                except: break
                time.sleep(5)
        threading.Thread(target=_watcher, daemon=True).start()

    # ─────────────────────────────────────── HELPERS ──────────────────────────
    def _log(self, msg: str, tag: str = ''):
        def _i():
            self.log_box.configure(state=tk.NORMAL)
            # Smart autoscroll: faqat user pastda bo'lsa scroll qilamiz
            at_bottom = self.log_box.yview()[1] >= 0.98
            if tag:
                self.log_box.insert(tk.END, msg + '\n', tag)
            else:
                self.log_box.insert(tk.END, msg + '\n')
            # Log cheklovi: eng eski qatorlarni o'chirish
            line_count = int(self.log_box.index('end-1c').split('.')[0])
            if line_count > self.LOG_MAX_LINES:
                excess = line_count - self.LOG_MAX_LINES
                self.log_box.delete('1.0', f'{excess + 1}.0')
            if at_bottom:
                self.log_box.see(tk.END)
            self.log_box.configure(state=tk.DISABLED)
        self.root.after(0, _i)

    def _status(self, msg: str):
        self.root.after(0, lambda: self.status_var.set(msg))

    def _prog(self, p: float):
        self.root.after(0, lambda: self.prog_var.set(p))

    def _eta(self, msg: str):
        self.root.after(0, lambda: self.eta_var.set(msg))

    def _update_speed(self):
        if self._dl_total <= 0 or self._dl_start_time <= 0 or self._dl_done_count <= 0:
            return
        elapsed  = time.time() - self._dl_start_time
        speed    = self._dl_done_count / elapsed         # ch/sec
        remain   = self._dl_total - self._dl_done_count
        eta_sec  = remain / speed if speed > 0 else 0
        spd_min  = speed * 60
        if eta_sec < 60:
            eta_s = f'~{int(eta_sec)}s'
        elif eta_sec < 3600:
            eta_s = f'~{int(eta_sec/60)}min'
        else:
            eta_s = f'~{eta_sec/3600:.1f}h'
        self._eta(f'⚡ {spd_min:.1f} ch/min  |  ETA {eta_s}')

    def _do_stop(self):
        self._stop = True
        self._log("To'xtatish soralmoqda...", 'warn')

    def _choose_out(self):
        cur = self.out_var.get().strip()
        init_dir  = os.path.dirname(cur) if os.path.dirname(cur) else SCRIPT_DIR
        init_file = os.path.basename(cur) or 'novel.docx'
        p = filedialog.asksaveasfilename(
            defaultextension='.docx',
            filetypes=[('Word hujjat','*.docx'), ('Barcha fayllar','*.*')],
            initialdir=init_dir,
            initialfile=init_file)
        if p:
            # .docx extension majburiy
            if not p.lower().endswith('.docx'):
                p += '.docx'
            self.out_var.set(p)

    def _del_cache(self):
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning('Xato', 'URL kiriting'); return
        clear_cache(normalize_url(url))
        self._chapters = []
        self.range_lbl.config(text='(avval Skan bajaring)')
        self.scan_info.config(text='Cache o\'chirildi. Qayta Skan bajariladi.',
                               fg='#f0a500')
        self._log('Cache o\'chirildi.', 'warn')

    # ─────────────────────────────────────── CAPTCHA ──────────────────────────
    def _show_captcha_banner(self, url: str):
        self._captcha_waiting = True
        self._captcha_url     = url
        def _ui():
            self.captcha_url_lbl.config(text=f'URL: {url[:110]}')
            self.captcha_frame.pack(fill=tk.X, pady=(0,6), before=self.prog_frame)
        self.root.after(0, _ui)

    def _hide_captcha_banner(self):
        self.root.after(0, lambda: self.captcha_frame.pack_forget())

    def _captcha_manual_solve(self):
        if not check_proxy():
            messagebox.showwarning(
                'CF Proxy yoq',
                'cf_proxy.py ishlamayapti!\n'
                'run_proxy.bat ni ishga tushiring, keyin qayta urinib ko\'ring.')
            return
        url = self._captcha_url or BASE + '/'
        self._log(f'🌐 Chromium ochilmoqda: {url}', 'info')
        self.manual_btn.configure(state=tk.DISABLED)

        def _run():
            try:
                r = requests.post(CF_PROXY_URL + '/manual_solve',
                                  json={'url': url}, timeout=325)
                data = r.json()
                if data.get('status') == 'solved':
                    self._log('✅ Captcha brauzerda hal qilindi!', 'ok')
                    self._captcha_waiting = False
                    self._hide_captcha_banner()
                else:
                    self._log(f'⚠️  Manual solve: {data.get("status","?")}', 'warn')
            except Exception as e:
                self._log(f'Manual solve xato: {e}', 'err')
            finally:
                self.root.after(0, lambda: self.manual_btn.configure(state=tk.NORMAL))

        threading.Thread(target=_run, daemon=True).start()

    def _captcha_resolved(self):
        """User qo'lda hal qildi."""
        self._captcha_waiting = False
        self._hide_captcha_banner()
        # Proxy session yangilash
        try: requests.post(CF_PROXY_URL + '/close_session', timeout=5)
        except: pass
        _invalidate_proxy_cache()
        self._log('✅ Captcha hal qilindi — yuklash davom etmoqda...', 'ok')

    # ─────────────────────────────────────── LOCK ─────────────────────────────
    def _lock_scan(self, on: bool):
        def _i():
            self.scan_btn.configure(state=tk.DISABLED if on else tk.NORMAL)
            self.stop_scan_btn.configure(state=tk.NORMAL if on else tk.DISABLED)
            self.start_btn.configure(state=tk.DISABLED if on else tk.NORMAL)
        self.root.after(0, _i)

    def _lock_dl(self, on: bool):
        def _i():
            self.start_btn.configure(state=tk.DISABLED if on else tk.NORMAL)
            self.scan_btn.configure(state=tk.DISABLED if on else tk.NORMAL)
            self.stop_btn.configure(state=tk.NORMAL if on else tk.DISABLED)
        self.root.after(0, _i)

    # ─────────────────────────────────────── SCAN ─────────────────────────────
    def _start_scan(self):
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning('Xato', 'URL kiriting!'); return

        norm = normalize_url(url)
        self._start_url = norm
        self._stop = False

        cached = load_cache(norm)
        if cached:
            self._chapters = cached
            self._on_scan_done(from_cache=True)
            return

        self._lock_scan(True)
        self._prog(0)
        self._log(f'\n{"─"*54}', 'head')
        self._log(f'SKAN: {norm}', 'info')

        sess = make_session()

        def pcb(count, title, has_next):
            self._status(f'Skan: {count} ta — {(title or "")[:40]}')
            self._prog(min(count * 0.5, 95))

        def _run():
            try:
                ch = scan_chapters(norm, sess, log=self._log,
                                   stop_flag=lambda: self._stop, progress_cb=pcb)
                self._chapters = ch
                if ch: save_cache(ch, norm)
                self.root.after(0, self._on_scan_done)
            except Exception as e:
                import traceback
                self._log(f'Skan xato: {e}', 'err')
            finally:
                self._lock_scan(False)

        threading.Thread(target=_run, daemon=True).start()

    def _on_scan_done(self, from_cache=False):
        chs = self._chapters
        if not chs:
            self.root.after(0, lambda: self.scan_info.config(
                text='Chapter topilmadi. URL ni tekshiring.', fg=self.RED))
            self._status('Skan xato')
            return

        nums = [c['num'] for c in chs]
        mn, mx = min(nums), max(nums)
        src = ' (cache)' if from_cache else ''

        def _ui():
            self.scan_info.config(
                text=f'✔  {len(chs)} ta chapter topildi{src}: CH{mn} — CH{mx}',
                fg=self.GRN)
            self.from_var.set(str(mn))
            self.to_var.set(str(mx))
            self.range_lbl.config(text=f'jami {len(chs)} ta  (CH{mn} – CH{mx})')
            name = re.sub(r'[<>:"/\\|?*]', '_',
                          get_novel_name(self._start_url) if self._start_url else 'novel')
            self.out_var.set(os.path.join(SCRIPT_DIR, name + '.docx'))

        self.root.after(0, _ui)
        self._prog(100)
        self._status(f'Skan tugadi: {len(chs)} ta chapter')
        self._log(f'Skan tugadi: {len(chs)} ta  (CH{mn}–CH{mx})', 'ok')
        if from_cache:
            self._log('(Cache dan yuklandi. Yangilash uchun "Cache" tugmasini bosing)', 'info')

    # ─────────────────────────────────────── DOWNLOAD ─────────────────────────
    def _start_download(self):
        if not self._chapters:
            messagebox.showwarning('Xato', 'Avval Skan bajaring!'); return
        try:
            fn = int(self.from_var.get())
            tn = int(self.to_var.get())
            assert fn <= tn
        except Exception:
            messagebox.showwarning('Xato', 'Raqamlarni to\'g\'ri kiriting!  Dan ≤ Gacha')
            return
        out = self.out_var.get().strip()
        # .docx extension majburiy
        if out and not out.lower().endswith('.docx'):
            out += '.docx'
            self.out_var.set(out)
        if not out:
            messagebox.showwarning('Xato', 'Saqlash joyini kiriting!'); return

        self._stop            = False
        self._captcha_waiting = False
        self._lock_dl(True)
        self._prog(0)
        self._eta('')

        def _run():
            try: self._download(fn, tn, out)
            except Exception as e:
                import traceback
                self._log(f'Yuklash xato: {e}\n{traceback.format_exc()}', 'err')
            finally: self._lock_dl(False)

        threading.Thread(target=_run, daemon=True).start()

    def _download(self, from_num: int, to_num: int, out_path: str):
        target = [c for c in self._chapters if from_num <= c['num'] <= to_num]
        if not target:
            self._log(f'CH{from_num}–CH{to_num} orasida chapter yo\'q!', 'err')
            self._status('Xato'); return

        target_nums = [c['num'] for c in target]
        total = len(target)

        # ── Resume check ──
        collected, failed, start_idx = [], [], 0
        prog_data = load_progress(out_path)
        if prog_data and prog_data.get('target_nums') == target_nums:
            prev = prog_data.get('collected', [])
            if prev:
                resume = messagebox.askyesno(
                    'Davom ettirish',
                    f'{len(prev)} ta chapter allaqachon yuklangan.\n'
                    f'Qolgan {total - len(prev)} ta dan davom ettirilsinmi?')
                if resume:
                    collected  = [(d['title'], d['paras']) for d in prev]
                    failed     = prog_data.get('failed', [])
                    start_idx  = len(collected)
                    self._log(f'Davom: {start_idx} dan boshlab...', 'info')
                else:
                    clear_progress(out_path)

        self._log(f'\n{"─"*54}', 'head')
        self._log(f'Yuklanmoqda: CH{from_num}–CH{to_num}  ({total} ta)')
        if start_idx:
            self._log(f'(Allaqachon: {start_idx} ta  |  Qoldi: {total-start_idx} ta)', 'info')

        sess        = make_session()
        using_proxy = check_proxy()
        sess_chapter_count = 0   # session yangilash uchun hisoblagich

        if using_proxy:
            self._log('✅ CF Proxy aniqlandi — real Chrome ishlatiladi', 'ok')
        else:
            self._log('ℹ️  cloudscraper rejimi (CF Proxy topilmadi)', 'info')

        prev_url = target[start_idx-1]['url'] if start_idx > 0 else BASE + '/'

        self._dl_start_time  = time.time()
        self._dl_done_count  = 0
        self._dl_total       = total - start_idx

        for i, ch in enumerate(target[start_idx:], start=start_idx):
            # ── Stop check ──
            if self._stop:
                self._log("To'xtatildi.")
                save_progress(out_path, collected, failed, from_num, to_num, target_nums)
                self._log(f'Progress saqlandi ({len(collected)}/{total}). '
                           'Keyingi safar davom ettirish mumkin.', 'info')
                break

            self._status(f'[{i+1}/{total}] CH{ch["num"]}...')
            self._prog(int((i / total) * 95))
            self._update_speed()

            # Per-chapter proxy check (uses cache — cheap)
            using_proxy = check_proxy()

            # Cloudscraper session yangilash (har 80 chapterda) — cookie eskirib qolmasin
            sess_chapter_count += 1
            if not using_proxy and sess_chapter_count % 80 == 0:
                self._log(f'  [Session] Cookie yangilanmoqda...', 'info')
                sess = make_session()

            soup, _, captcha = smart_fetch(sess, ch['url'], self._log,
                                           stop_flag=lambda: self._stop,
                                           referer=prev_url)

            # ── CAPTCHA PAUSE / RESUME ────────────────────────────────
            if captcha:
                self._log(f'🔴 CAPTCHA aniqlandi! CH{ch["num"]} — yuklash to\'xtatildi', 'err')
                self._show_captcha_banner(ch['url'])
                save_progress(out_path, collected, failed, from_num, to_num, target_nums)

                # Block worker thread until user resolves
                while self._captcha_waiting and not self._stop:
                    time.sleep(0.5)

                if self._stop:
                    self._log('Captcha kutilayotganda to\'xtatildi.', 'warn')
                    break

                # Brief stabilisation wait
                self._log(f'  Qayta urinish: CH{ch["num"]}...', 'info')
                time.sleep(3)

                # Retry same chapter
                soup, _, captcha2 = smart_fetch(sess, ch['url'], self._log,
                                                stop_flag=lambda: self._stop,
                                                referer=prev_url)
                if captcha2 or soup is None:
                    self._log(f'  [{i+1}/{total}] XATO: CH{ch["num"]} '
                               '(captcha hal qilinmadi?)', 'err')
                    collected.append((ch.get('title', f'Chapter {ch["num"]}'), []))
                    failed.append(ch['num'])
                    save_progress(out_path, collected, failed, from_num, to_num, target_nums)
                    continue
            # ─────────────────────────────────────────────────────────

            if soup is None:
                self._log(f'  [{i+1}/{total}] XATO: CH{ch["num"]}', 'err')
                collected.append((ch.get('title', f'Chapter {ch["num"]}'), []))
                failed.append(ch['num'])
            else:
                paras = extract_content(soup)
                ttl, _ = find_chapter_title(soup)
                title_text = ttl or ch.get('title') or f'Chapter {ch["num"]}'
                self._log(f'  [{i+1}/{total}] OK: {title_text[:52]} ({len(paras)} para)', 'ok')
                collected.append((title_text, paras))
                prev_url = ch['url']
                self._dl_done_count += 1

            save_progress(out_path, collected, failed, from_num, to_num, target_nums)

            # ── Delay (fast with proxy, slower without) ──
            if using_proxy:
                delay = random.uniform(0.3, 0.7)
            else:
                delay = random.uniform(1.0, 2.5)
                if random.random() < 0.06:
                    extra = random.uniform(3, 7)
                    delay += extra
                    self._log(f'  (Qisqa tanaffus: {delay:.1f}s)', 'info')
            time.sleep(delay)

        if self._stop:
            return

        if not collected:
            self._log('Hech narsa yuklanmadi.', 'err')
            self._status('Xato'); return

        name = get_novel_name(self._start_url) if self._start_url else 'Novel'
        self._status('DOCX yaratilyapti...')
        self._eta('')
        try:
            build_docx(name, collected, out_path)
        except Exception as e:
            self._log(f'❌ DOCX yaratishda xato: {e}', 'err')
            self._log('  (Disk to\'liq yoki fayl boshqa dasturda ochiq bo\'lishi mumkin)', 'warn')
            self._status('DOCX xato — fayl saqlanmadi')
            return
        self._prog(100)
        clear_progress(out_path)

        n_ok = total - len(failed)

        elapsed = time.time() - self._dl_start_time
        spd = n_ok / elapsed * 60 if elapsed > 0 else 0

        self._log(f'\n{"─"*54}', 'head')
        self._log(f'✅ Saqlandi: {out_path}', 'ok')
        self._log(f'{n_ok}/{total} chapter OK  |  {len(failed)} ta xato')
        if failed: self._log(f'Xato chapterlar: {failed[:10]}{"..." if len(failed)>10 else ""}', 'warn')
        self._log(f'Tezlik: {spd:.1f} ch/min  |  Vaqt: {elapsed/60:.1f} min', 'info')
        self._status(f'Tayyor! → {os.path.basename(out_path)}')

        def _show_done_dialog(path=out_path, ok=n_ok, tot=total, fl=failed):
            win = tk.Toplevel(self.root)
            win.title('Tayyor!')
            win.configure(bg=self.BG)
            win.resizable(False, False)
            win.grab_set()
            pad = dict(padx=20, pady=10)
            icon = '✅' if not fl else '⚠️'
            tk.Label(win, text=f'{icon}  Yuklash yakunlandi',
                     bg=self.BG, fg=self.GRN if not fl else self.WARN,
                     font=('Segoe UI', 13, 'bold')).pack(anchor=tk.W, **pad)
            tk.Label(win, text=f'Fayl: {path}',
                     bg=self.BG, fg=self.FG, font=('Segoe UI', 9),
                     wraplength=460, justify=tk.LEFT).pack(anchor=tk.W, padx=20)
            tk.Label(win, text=f'{ok}/{tot} chapter muvaffaqiyatli' +
                     (f'   |   {len(fl)} ta xato' if fl else ''),
                     bg=self.BG, fg=self.GRAY, font=('Segoe UI', 9)).pack(
                     anchor=tk.W, padx=20, pady=(4, 12))
            btn_row = tk.Frame(win, bg=self.BG); btn_row.pack(padx=20, pady=(0,14))
            def _open_folder():
                folder = os.path.dirname(os.path.abspath(path))
                try:
                    import subprocess
                    if sys.platform == 'win32':
                        subprocess.Popen(['explorer', '/select,', os.path.abspath(path)])
                    elif sys.platform == 'darwin':
                        subprocess.Popen(['open', '-R', path])
                    else:
                        subprocess.Popen(['xdg-open', folder])
                except Exception: pass
                win.destroy()
            ttk.Button(btn_row, text='📂  Papkani ochish', style='Acc.TButton',
                       command=_open_folder).pack(side=tk.LEFT, padx=(0, 8))
            ttk.Button(btn_row, text='OK', style='Sml.TButton',
                       command=win.destroy).pack(side=tk.LEFT)

        self.root.after(0, _show_done_dialog)


# ── ENTRY ─────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    root = tk.Tk()
    App(root)
    root.mainloop()
