"""Front-end sanity: every JS file parses (a broken app.js silently disables offline queue and idempotency keys)."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
JS = sorted((ROOT / 'static' / 'js').glob('*.js')) + [ROOT / 'static' / 'service-worker.js']


@pytest.mark.skipif(not shutil.which('node'), reason='node yo‘q')
@pytest.mark.parametrize('path', JS, ids=lambda p: p.name)
def test_js_syntax(path):
    r = subprocess.run(['node', '--check', str(path)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_inline_scripts_in_templates_parse(tmp_path):
    if not shutil.which('node'):
        pytest.skip('node yo‘q')
    import re
    for tpl in (ROOT / 'templates').glob('*.html'):
        for i, block in enumerate(re.findall(r'<script>(.*?)</script>', tpl.read_text(encoding='utf-8'), re.S)):
            if '{{' in block or '{%' in block:
                block = re.sub(r'\{\{.*?\}\}', '0', block)
                block = re.sub(r'\{%.*?%\}', '', block)
            f = tmp_path / f'{tpl.stem}_{i}.js'
            f.write_text(block)
            r = subprocess.run(['node', '--check', str(f)], capture_output=True, text=True)
            assert r.returncode == 0, f'{tpl.name}: {r.stderr}'
