"""Take screenshots of the running app (desktop, phone, TV). Usage: python tools/screens.py BASE_URL OUT_DIR"""
import sys
from playwright.sync_api import sync_playwright

BASE, OUT = sys.argv[1], sys.argv[2]
EXE = '/opt/pw-browsers/chromium-1194/chrome-linux/chrome'


def login(page, user, pw):
    page.goto(BASE + '/login')
    page.fill('input[name=username]', user)
    page.fill('input[name=password]', pw)
    page.click('button.btn-primary')
    page.wait_for_load_state('networkidle')


with sync_playwright() as p:
    import glob
    exe = (glob.glob('/opt/pw-browsers/chromium-*/chrome-linux/chrome') or [None])[0]
    b = p.chromium.launch(executable_path=exe)
    d = b.new_page(viewport={'width': 1536, 'height': 1024})
    d.goto(BASE + '/login'); d.screenshot(path=f'{OUT}/00_login.png')
    login(d, 'admin', sys.argv[3] if len(sys.argv) > 3 else 'Admin2026x')
    for name, url in [('01_dashboard', '/?view=full'), ('02_terim', '/terim'), ('03_telashkalar', '/telashkalar'),
                      ('04_tarozi', '/tarozi'), ('05_nakladnoylar', '/nakladnoylar'), ('06_nakladnoy', '/nakladnoy/1'),
                      ('07_nayman', '/nayman'), ('08_hisobotlar', '/hisobotlar'), ('09_kassa', '/kassa'),
                      ('10_integratsiya', '/admin/integratsiyalar'), ('11_foydalanuvchilar', '/admin/foydalanuvchilar'),
                      ('12_dalalar', '/admin/dala/1')]:
        d.goto(BASE + url); d.wait_for_load_state('networkidle'); d.wait_for_timeout(600)
        d.screenshot(path=f'{OUT}/{name}.png', full_page=name in ('01_dashboard',))
    tv = b.new_page(viewport={'width': 1920, 'height': 1080})
    tv.context.add_cookies(d.context.cookies())
    tv.goto(BASE + '/tv'); tv.wait_for_timeout(1500); tv.screenshot(path=f'{OUT}/20_tv.png')
    ph = b.new_context(viewport={'width': 390, 'height': 844}, device_scale_factor=2, is_mobile=True, has_touch=True,
                       user_agent='Mozilla/5.0 (Linux; Android 13; SM-A135F) AppleWebKit/537.36 Chrome/124 Mobile Safari/537.36').new_page()
    for user, tag in (('juma', 'brigadir'), ('tarozi01', 'tarozi'), ('rahbar', 'rahbar')):
        ph.context.clear_cookies()
        login(ph, user, 'Demo2026!')
        ph.screenshot(path=f'{OUT}/30_tel_{tag}_bosh.png')
    ph.context.clear_cookies(); login(ph, 'juma', 'Demo2026!')
    ph.goto(BASE + '/terim'); ph.wait_for_load_state('networkidle'); ph.screenshot(path=f'{OUT}/31_tel_brigadir_terim.png', full_page=True)
    b.close()
