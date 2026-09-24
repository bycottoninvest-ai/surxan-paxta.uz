"""Measure phone page speed on throttled networks via Chrome DevTools.

Usage: python tools/perf.py BASE_URL [user] [password]
Prints a table: page, network profile, bytes transferred, DOMContentLoaded, load, first contentful paint.
"""
import glob
import json
import sys

from playwright.sync_api import sync_playwright

BASE = sys.argv[1]
USER = sys.argv[2] if len(sys.argv) > 2 else 'juma'
PW = sys.argv[3] if len(sys.argv) > 3 else 'Demo2026!'
PROFILES = {
    # Chrome DevTools presets (download/upload bytes per second, latency ms)
    'Slow 3G (400 kbit/s, 400 ms)': (400 * 1024 / 8, 400 * 1024 / 8, 400),
    'Juda sekin 2G (250 kbit/s, 800 ms)': (250 * 1024 / 8, 50 * 1024 / 8, 800),
}
PAGES = [('/', 'Brigadir bosh sahifa'), ('/terim', 'Terim kiritish'), ('/telashkalar', 'Telashkalar')]
PHONE_UA = 'Mozilla/5.0 (Linux; Android 13; SM-A135F) AppleWebKit/537.36 Chrome/124 Mobile Safari/537.36'

results = []
with sync_playwright() as p:
    exe = (glob.glob('/opt/pw-browsers/chromium-*/chrome-linux/chrome') or [None])[0]
    browser = p.chromium.launch(executable_path=exe)
    for prof, (down, up, lat) in PROFILES.items():
        for cold in (True, False):
            ctx = browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True,
                                      user_agent=PHONE_UA, service_workers='block')
            page = ctx.new_page()
            # log in without throttling first
            page.goto(BASE + '/login')
            page.fill('input[name=username]', USER)
            page.fill('input[name=password]', PW)
            page.click('button.btn-primary')
            page.wait_for_load_state('networkidle')
            cdp = ctx.new_cdp_session(page)
            cdp.send('Network.enable')
            if cold:
                cdp.send('Network.setCacheDisabled', {'cacheDisabled': True})
            cdp.send('Network.emulateNetworkConditions', {'offline': False, 'latency': lat, 'downloadThroughput': down,
                                                          'uploadThroughput': up})
            cdp.send('Emulation.setCPUThrottlingRate', {'rate': 4})
            sizes = []
            cdp.on('Network.loadingFinished', lambda e: sizes.append(e.get('encodedDataLength', 0)))
            for url, label in PAGES:
                sizes.clear()
                page.goto(BASE + url, wait_until='load', timeout=120000)
                t = page.evaluate('''() => { const n = performance.getEntriesByType('navigation')[0];
                    const fcp = performance.getEntriesByName('first-contentful-paint')[0];
                    return {dcl: n.domContentLoadedEventEnd, load: n.loadEventEnd, fcp: fcp ? fcp.startTime : null}; }''')
                page.wait_for_timeout(300)
                results.append({'page': label, 'profile': prof, 'cache': 'birinchi ochilish' if cold else 'qayta ochilish',
                                'kb': round(sum(sizes) / 1024, 1), 'fcp_ms': round(t['fcp'] or 0),
                                'dcl_ms': round(t['dcl']), 'load_ms': round(t['load'])})
            ctx.close()
    browser.close()

print(f"{'Sahifa':24} {'Tarmoq':34} {'Kesh':18} {'KB':>7} {'FCP ms':>8} {'DCL ms':>8} {'Load ms':>8}")
for r in results:
    print(f"{r['page']:24} {r['profile']:34} {r['cache']:18} {r['kb']:>7} {r['fcp_ms']:>8} {r['dcl_ms']:>8} {r['load_ms']:>8}")
json.dump(results, open('perf_results.json', 'w'), ensure_ascii=False, indent=1)
