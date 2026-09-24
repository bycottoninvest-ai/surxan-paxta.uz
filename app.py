"""Entry point: `gunicorn app:app` (production) or `python app.py` (local)."""
import os
from pathlib import Path

# Load .env for local runs (Docker passes the variables itself).
_env = Path(__file__).with_name('.env')
if _env.exists():
    for line in _env.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

from surxon import create_app  # noqa: E402

app = create_app()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=os.environ.get('DEBUG', '0') == '1')
