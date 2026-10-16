"""Reads the kg columns of a STARSHI TALONI sheet photo with Claude (vision) — a suggestion for the clerk, never a payment.

Privacy: only the part right of the ▼…▲ line printed on the sheet is sent (Abetgacha / Abetdan keyin / JAMI and the
row number) — the names stay on paper and in our own photo archive. The clerk moves that line on the phone if needed.

Safety: rows that do not add up (before + after ≠ total) or were unclear come back flagged; the clerk fixes them by
looking at the photo and confirms — only then the kg is written (as before, through tezpul.enter).

The key: the server's .env (ANTHROPIC_API_KEY) if set, else the one the admin pasted on Admin → Integratsiyalar — kept
like the Google Sheets key, in a private file next to the database (never in the database, the backups, the audit or on
any page; only its last 4 characters are shown). Without a key the feature stays off and the photo is still kept as
evidence. The model is a setting (tezpul_ai_model) — Claude Opus 5.5 by default, Claude Haiku 5.5 is cheaper.
"""
import base64
import io
import json
import os
import re
from pathlib import Path

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


KEY_FILE = 'anthropic-api.key'
KEY_RE = re.compile(r'sk-ant-[A-Za-z0-9_-]{20,}')


def _key_path():
    return Path(current_app.config['SURXON'].DATA_DIR) / KEY_FILE


def _saved_key():
    try:
        return _key_path().read_text(encoding='utf-8').strip()
    except OSError:
        return ''


def api_key():
    return current_app.config['SURXON'].ANTHROPIC_API_KEY or _saved_key()


def source():
    """'env' (server .env), 'site' (pasted on the Integratsiyalar page) or '' (none)."""
    if current_app.config['SURXON'].ANTHROPIC_API_KEY:
        return 'env'
    return 'site' if _saved_key() else ''


def key_tail():
    k = api_key()
    return ('…' + k[-4:]) if k else ''


def _verify(key):
    """Asks Anthropic whether the key works (the model list — free, nothing is read). True / False, None: no answer."""
    import anthropic
    try:
        anthropic.Anthropic(api_key=key, timeout=20, max_retries=1).models.list(limit=1)
        return True
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
        return False
    except anthropic.APIError:
        return None


def save_key(raw):
    """The key pasted by the admin: checked with Anthropic, then written to the private file. Returns True when
    Anthropic confirmed it, None when it could not be asked (saved anyway)."""
    key = re.sub(r'\s+', '', raw or '')
    if not KEY_RE.fullmatch(key):
        raise UserError('Kalit ko‘rinishi noto‘g‘ri — platform.claude.com → Ключи API dan “sk-ant-…” bilan boshlanadigan '
                        'kalitni to‘liq nusxalang.')
    ok = _verify(key)
    if ok is False:
        raise UserError('Anthropic bu kalitni qabul qilmadi — kalit o‘chirilgan yoki to‘liq nusxalanmagan. Yangi kalit yarating.')
    path = _key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as out:
        out.write(key)
    os.replace(tmp, path)
    return ok


def clear_key():
    _key_path().unlink(missing_ok=True)


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
    return _request(jpeg_bytes, PROMPT, SCHEMA)


def _request(jpeg_bytes, prompt, schema):
    """One request to Claude: the photo and the instructions; the answer is JSON of `schema`."""
    import anthropic
    client = anthropic.Anthropic(api_key=api_key(), timeout=90, max_retries=2)
    try:
        resp = client.beta.messages.create(
            model=model(),
            max_tokens=16000,
            betas=['server-side-fallback-2026-07-01'],
            fallbacks='default',
            output_config={'format': {'type': 'json_schema', 'schema': schema}},
            messages=[{'role': 'user', 'content': [
                {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg',
                                             'data': base64.standard_b64encode(jpeg_bytes).decode()}},
                {'type': 'text', 'text': prompt}]}],
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


# ------------------------------------------------------------------ DALA NAKLADNOYI (PB v3) — read at the punkt / sent to the bot

PB_PROMPT = """This photo shows a filled paper form "DALA NAKLADNOYI" (a cotton trailer's field waybill), filled in by hand.
Read it:
- "blank_no": the printed red number like "PB-0123" (top right or on the left stub).
- "trailer": the handwritten trailer number after "Telashka: TL-" (only the number part, e.g. "02").
- "method": "hand" if the box "QO‘L TERIMI" is ticked, "combine" if "KOMBAYN" is ticked, null if neither.
- "combine_no": what is written after "KOMBAYN №" (or null).
- "rows": the table rows 1, 2, 3. For each row that has anything written: "brigadier" = the printed name next to the
  ticked box (one of: {names}), or the handwritten name next to the empty box if that one is ticked; "field" = the field
  code written in the "Dala" column (like "D-04" or "4"); "kg" = the number in the "Kg" column. Ignore the "Imzo"
  (signature) column. Set "unclear": true if any of the row's handwriting is not clearly readable or two boxes are ticked.
- "total_kg": the number written in "JAMI KG".
- "date": the date written after "Sana" (as written) or null.
Handwriting may be in Uzbek (Latin or Cyrillic) or in Russian: e.g. "Бойрам ота"/"Байрам" = Bayram ota, "ТЛ-02" = TL-02,
"Д-4"/"поле 4" = field 4, "кг", "итого"/"жами" = total. Map a name written in Cyrillic to the matching name from the list
above (give the list's Latin spelling); if it matches none, copy it as written and set "unclear": true.
Copy exactly what is written — do not correct numbers or fill in missing cells (leave them null). If the photo is not
this form, return empty rows and explain in "note" (in Uzbek)."""

PB_SCHEMA = {
    'type': 'object',
    'properties': {
        'blank_no': {'anyOf': [{'type': 'string'}, {'type': 'null'}]},
        'trailer': {'anyOf': [{'type': 'string'}, {'type': 'null'}]},
        'method': {'anyOf': [{'type': 'string', 'enum': ['hand', 'combine']}, {'type': 'null'}]},
        'combine_no': {'anyOf': [{'type': 'string'}, {'type': 'null'}]},
        'rows': {'type': 'array', 'items': {
            'type': 'object',
            'properties': {'row': {'type': 'integer'}, 'brigadier': {'anyOf': [{'type': 'string'}, {'type': 'null'}]},
                           'field': {'anyOf': [{'type': 'string'}, {'type': 'null'}]}, 'kg': _NUM,
                           'unclear': {'type': 'boolean'}},
            'required': ['row', 'brigadier', 'field', 'kg', 'unclear'], 'additionalProperties': False}},
        'total_kg': _NUM,
        'date': {'anyOf': [{'type': 'string'}, {'type': 'null'}]},
        'note': {'type': 'string'},
    },
    'required': ['blank_no', 'trailer', 'method', 'combine_no', 'rows', 'total_kg', 'date', 'note'],
    'additionalProperties': False,
}


def photo_jpeg(data):
    """The photo upright and at most MAX_SIDE px, as JPEG (for reading and for the archive)."""
    full, _ = kg_part(data, 0)
    return full


def _ask_pb(jpeg_bytes, names):
    return _request(jpeg_bytes, PB_PROMPT.replace('{names}', ', '.join(f'"{n}"' for n in names) or '—'), PB_SCHEMA)


def read_blank(jpeg_bytes, names):
    """The field waybill read from its photo: {blank_no, trailer, method, combine_no, rows, total_kg, date, note}."""
    return _ask_pb(jpeg_bytes, names)
