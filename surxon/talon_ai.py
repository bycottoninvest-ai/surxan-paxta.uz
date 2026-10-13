"""Reads the kg columns of a STARSHI TALONI sheet photo with Claude (vision) — a suggestion for the clerk, never a payment.

Privacy: only the part right of the ▼…▲ line printed on the sheet is sent (Abetgacha / Abetdan keyin / JAMI and the
row number) — the names stay on paper and in our own photo archive. The clerk moves that line on the phone if needed.

Safety: rows that do not add up (before + after ≠ total) or were unclear come back flagged; the clerk fixes them by
looking at the photo and confirms — only then the kg is written (as before, through tezpul.enter).

The key is read from the server's .env only (ANTHROPIC_API_KEY); without it the feature stays off and the photo is still
kept as evidence. The model is a setting (tezpul_ai_model) — Claude Opus 5.5 by default, Claude Haiku 5.5 is cheaper.
"""
import base64
import io
import json

from flask import current_app

from .settings import get_setting
from .utils import UserError

MODELS = {'claude-opus-5-5': 'Claude Opus 5.5 (aniqroq)', 'claude-haiku-5-5': 'Claude Haiku 5.5 (arzon)'}
DEFAULT_MODEL = 'claude-opus-5-5'
MAX_SIDE = 1600                     # px — enough for handwriting, keeps each request small

PROMPT = """This photo shows the right part of one sheet of a handwritten cotton-picking list (a printed table, filled in \
by hand with a pen). Columns from left to right:
1. "Abetgacha" — kg picked before lunch
2. "Abetdan keyin" — kg picked after lunch
3. "JAMI" — the person's total kg
4. "№" — the printed row number (small, on the right)
The last row of the table (pink, marked "Σ", "varaq jami") is the sheet's total row.

Read every row that has any handwritten number. Numbers are kilograms; a comma may be a decimal separator (37,5 = 37.5).
Rules:
- Copy exactly what is written. Do not correct the arithmetic and do not fill in a missing cell — leave it null.
- If any digit of a cell is not clearly readable, give your best reading and set "unclear": true for that row.
- Skip rows that are completely empty. Do not invent rows.
- Put the "Σ" row into "sheet_total", not into "rows".
- If the photo is not such a table or is unreadable, return empty rows and explain in "note"."""

_NUM = {'anyOf': [{'type': 'number'}, {'type': 'null'}]}
SCHEMA = {
    'type': 'object',
    'properties': {
        'rows': {'type': 'array', 'items': {
            'type': 'object',
            'properties': {'row': {'type': 'integer'}, 'before': _NUM, 'after': _NUM, 'total': _NUM,
                           'unclear': {'type': 'boolean'}},
            'required': ['row', 'before', 'after', 'total', 'unclear'], 'additionalProperties': False}},
        'sheet_total': {'type': 'object', 'properties': {'before': _NUM, 'after': _NUM, 'total': _NUM},
                        'required': ['before', 'after', 'total'], 'additionalProperties': False},
        'note': {'type': 'string'},
    },
    'required': ['rows', 'sheet_total', 'note'], 'additionalProperties': False,
}


def api_key():
    return current_app.config['SURXON'].ANTHROPIC_API_KEY


def enabled():
    return bool(api_key())


def model():
    m = (get_setting('tezpul_ai_model') or '').strip()
    return m if m in MODELS else DEFAULT_MODEL


def kg_part(data, cut):
    """The photo, upright, cut at `cut` (0…0.9 of the width): returns (the full photo for the archive, the kg part to
    read) — both JPEG, at most MAX_SIDE px."""
    from PIL import Image, ImageOps
    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert('RGB')
    except Exception:
        raise UserError('Rasm ochilmadi — qaytadan rasmga oling.')
    cut = min(max(float(cut or 0), 0.0), 0.9)

    def jpeg(im):
        im = im.copy()
        im.thumbnail((MAX_SIDE, MAX_SIDE))
        out = io.BytesIO()
        im.save(out, 'JPEG', quality=85)
        return out.getvalue()
    return jpeg(img), jpeg(img.crop((int(img.width * cut), 0, img.width, img.height)))


def _ask(jpeg_bytes):
    """One request to Claude with the kg part of the sheet. Returns the parsed JSON (SCHEMA)."""
    import anthropic
    client = anthropic.Anthropic(api_key=api_key(), timeout=90, max_retries=2)
    try:
        resp = client.beta.messages.create(
            model=model(),
            max_tokens=16000,
            betas=['server-side-fallback-2026-07-01'],
            fallbacks='default',
            output_config={'format': {'type': 'json_schema', 'schema': SCHEMA}},
            messages=[{'role': 'user', 'content': [
                {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg',
                                             'data': base64.standard_b64encode(jpeg_bytes).decode()}},
                {'type': 'text', 'text': PROMPT}]}],
        )
    except anthropic.AuthenticationError:
        raise UserError('AI kaliti noto‘g‘ri — Admin → Integratsiyalar (server .env dagi ANTHROPIC_API_KEY).')
    except anthropic.RateLimitError:
        raise UserError('AI hozir band — bir daqiqadan keyin qayta yuboring yoki kg ni qo‘lda yozing.')
    except anthropic.APIConnectionError:
        raise UserError('AI ga ulanib bo‘lmadi (internet) — kg ni qo‘lda yozing, rasm saqlandi.')
    except anthropic.APIStatusError as err:
        raise UserError(f'AI xatosi ({err.status_code}) — kg ni qo‘lda yozing, rasm saqlandi.')
    if resp.stop_reason == 'refusal':
        raise UserError('AI bu rasmni o‘qimadi — kg ni qo‘lda yozing, rasm saqlandi.')
    text = next((b.text for b in resp.content if b.type == 'text'), '')
    try:
        return json.loads(text)
    except ValueError:
        raise UserError('AI javobi to‘liq kelmadi — qayta yuboring yoki kg ni qo‘lda yozing.')


def _n(v):
    try:
        return None if v is None else round(float(v), 1)
    except (TypeError, ValueError):
        return None


def check(raw):
    """The reading made safe to show: every row's total (as written, or before + after when the total cell is empty),
    a flag on rows that do not add up or were unclear, and the sheet sum against the Σ row."""
    rows, seen = [], set()
    for r in sorted(raw.get('rows') or [], key=lambda x: x.get('row') or 0):
        no = r.get('row')
        if not isinstance(no, int) or no < 1 or no in seen:
            continue
        seen.add(no)
        a, b, t = _n(r.get('before')), _n(r.get('after')), _n(r.get('total'))
        flag = ''
        if r.get('unclear'):
            flag = 'unclear'
        if a is not None and b is not None and t is not None and abs(a + b - t) > 0.05:
            flag = 'sum'
        if t is None:
            if a is None and b is None:
                continue
            t = round((a or 0) + (b or 0), 1)
            flag = flag or ('half' if a is None or b is None else '')
        rows.append({'row': no, 'before': a, 'after': b, 'total': t, 'flag': flag})
    st = raw.get('sheet_total') or {}
    computed = round(sum(r['total'] for r in rows), 1)
    written = _n(st.get('total'))
    return {'rows': rows, 'computed': computed, 'written_total': written,
            'total_ok': written is None or abs(written - computed) <= 0.05,
            'problems': sum(1 for r in rows if r['flag']), 'note': (raw.get('note') or '')[:300]}


def read_sheet(jpeg_bytes):
    return check(_ask(jpeg_bytes))
