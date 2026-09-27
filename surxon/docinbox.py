"""Document inbox: send every paper at once (to the bot or the site) — the system sorts it by firm and type itself.

Each file's text is read (PDF, Excel, Word, text); a STIR (INN), a firm name or a contract number found in it ties the
file to that firm (and contract), and words like “график”, “счет-фактура”, “акт сверки” give its type. A file with a
clear firm is filed at once into that firm's folder; the rest wait in “Hujjatlar qutisi” for one tap (firm + type).
A file already sent is never stored twice.
"""
import hashlib
import io
import re
import zipfile
from pathlib import Path

from flask import current_app

from . import loans as L
from .db import q, tx
from .security import audit
from .settings import get_setting
from .utils import UserError, clean_text, now_str

EXTS = ('.pdf', '.jpg', '.jpeg', '.png', '.webp', '.xlsx', '.xls', '.doc', '.docx', '.zip', '.csv', '.txt')
FILE_KINDS = {'shartnoma': 'Shartnoma', 'grafik': 'To‘lov grafigi', 'faktura': 'Faktura', 'akt': 'Akt-sverka',
              'kochirma': 'Bank ko‘chirmasi', 'polis': 'Sug‘urta polisi', 'rasm': 'Texnika rasmi', 'boshqa': 'Boshqa'}
# first match wins — the more exact words first
KIND_WORDS = [
    ('akt', ('акт сверки', 'акт-сверк', 'akt-sverka', 'akt sverka', 'солиштириш далолатнома', 'solishtirish dalolatnoma',
             'o‘zaro hisob', 'ўзаро ҳисоб')),
    ('faktura', ('счет-фактура', 'счёт-фактура', 'hisob-faktura', 'ҳисоб-фактура', 'invoice', 'faktura')),
    ('kochirma', ('maqsad nomi', 'выписка', 'ko‘chirma', 'кўчирма', 'hisobvaraqdan ko', 'остаток на начало', 'қолдиқ кун бошига')),
    ('grafik', ('график', 'grafik', 'тўлов жадвали', 'to‘lov jadvali', 'погашени')),
    ('polis', ('полис', 'polis')),
    ('shartnoma', ('шартнома', 'shartnoma', 'договор', 'contract')),
]


def _norm(s):
    return (s or '').replace('ʻ', '‘').replace("'", '‘').replace('`', '‘').replace('’', '‘').lower()


def extract_text(name, data):
    """Plain text of a document (first ~30 000 chars) — empty when it cannot be read (a photo, a scan)."""
    ext = Path(name or '').suffix.lower()
    try:
        if data[:4] == b'%PDF':
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            return '\n'.join((p.extract_text() or '') for p in reader.pages[:15])[:30000]
        if ext == '.xlsx' or (data[:2] == b'PK' and ext not in ('.docx', '.zip')):
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
            out = []
            for ws in wb.worksheets[:5]:
                for r in ws.iter_rows(values_only=True, max_row=400):
                    out.append(' '.join(str(c) for c in r if c is not None))
            return '\n'.join(out)[:30000]
        if ext == '.docx':
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                xml = z.read('word/document.xml').decode('utf-8', 'ignore')
            xml = re.sub(r'</w:p>', '\n', xml)
            return re.sub(r'<[^>]+>', '', xml)[:30000]
        if ext in ('.txt', '.csv'):
            for enc in ('utf-8', 'cp1251'):
                try:
                    return data.decode(enc)[:30000]
                except UnicodeDecodeError:
                    pass
    except Exception as exc:                                 # a broken file is still kept — only unsorted
        current_app.logger.info('docinbox: cannot read %s: %s', name, exc)
    return ''


def guess(name, text):
    """(party_id, contract_id, kind) — None where nothing is certain."""
    t = _norm(text + '\n' + (name or ''))
    kind = 'rasm' if Path(name or '').suffix.lower() in ('.jpg', '.jpeg', '.png', '.webp') and not text.strip() else None
    if not kind:
        kind = next((k for k, words in KIND_WORDS if any(w in t for w in words)), None)
        if not kind and Path(name or '').suffix.lower() in ('.xlsx', '.xls') and ('график' in t or 'сана' in t):
            kind = 'grafik'
    own = re.sub(r'\D', '', get_setting('company_inn') or '')
    inns = {i for i in re.findall(r'(?<!\d)(\d{9})(?!\d)', re.sub(r'(?<=\d)[  ](?=\d{3}(?!\d))', '', text)) if i != own}
    parties = q('SELECT id, name, inn FROM parties')
    hit = {p['id'] for p in parties if p['inn'] and p['inn'] in inns}
    if not hit:                                               # the firm's name without its legal form and quotes
        for p in parties:
            core = re.sub(r'\b(mchj|aj|atb|xk|ooo|ao|llc|ltd|жамияти|mas‘uliyati cheklangan jamiyat)\b', '', _norm(p['name']))
            core = re.sub(r'[«»"“”]', '', core).strip(' .,-')
            if len(core) >= 5 and core in t:
                hit.add(p['id'])
    if not hit and 'agro_bank' in t:                          # Agrobank platform export names the bank only this way
        hit = {p['id'] for p in parties if 'agro' in _norm(p['name']) and 'bank' in _norm(p['name'])}
    party_id = next(iter(hit)) if len(hit) == 1 else None
    contract_id = None
    cq = q("SELECT id, party_id, number FROM contracts WHERE status<>'BEKOR' AND number IS NOT NULL AND length(number)>=3")
    cs = [c for c in cq if _norm(c['number']) in t and (party_id is None or c['party_id'] == party_id)]
    if len(cs) == 1:
        contract_id = cs[0]['id']
        party_id = party_id or cs[0]['party_id']
    elif party_id:
        only = q("SELECT id FROM contracts WHERE party_id=? AND status<>'BEKOR'", (party_id,))
        if len(only) == 1:
            contract_id = only[0]['id']
    return party_id, contract_id, kind


def receive(actor, name, data, source='web'):
    """Store one document; file it at once when its firm is clear. Returns a dict for the reply."""
    L._need(actor)
    if not data:
        raise UserError('Fayl bo‘sh.')
    if len(data) > 25 * 1024 * 1024:
        raise UserError(f'{name}: fayl juda katta (25 MB dan ko‘p).')
    ext = (Path(name or '').suffix.lower() or '.bin')[:6]
    if ext not in EXTS:
        raise UserError(f'{name}: bu turdagi faylni saqlab bo‘lmaydi (PDF, rasm, Excel, Word, ZIP).')
    sha = hashlib.sha256(data).hexdigest()
    old = q('SELECT * FROM doc_inbox WHERE sha256=?', (sha,), one=True)
    if old:
        return _result(old, duplicate=True)
    rel = f'hujjatlar/{sha[:16]}{ext}'
    path = Path(current_app.config['SURXON'].UPLOAD_DIR) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    text = extract_text(name, data)
    pid, cid, kind = guess(name, text)
    snippet = clean_text(re.sub(r'\s+', ' ', text), 300)
    with tx() as db:
        cur = db.execute('''INSERT INTO doc_inbox(name, path, sha256, size, source, snippet, guess_party_id, guess_contract_id,
                            guess_kind, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                         (clean_text(name, 160) or 'fayl', rel, sha, len(data), source, snippet, pid, cid, kind,
                          actor.user_id, now_str()))
        iid = cur.lastrowid
        audit(db, actor, 'CREATE', 'doc_inbox', iid, new={'name': name, 'party_id': pid, 'kind': kind})
    if pid:
        file_it(actor, iid, pid, kind or 'boshqa', cid)
    return _result(q('SELECT * FROM doc_inbox WHERE id=?', (iid,), one=True))


def _result(r, duplicate=False):
    p = q('SELECT name FROM parties WHERE id=?', (r['guess_party_id'],), one=True) if r['guess_party_id'] else None
    c = q('SELECT title FROM contracts WHERE id=?', (r['guess_contract_id'],), one=True) if r['guess_contract_id'] else None
    return {'id': r['id'], 'name': r['name'], 'status': r['status'], 'duplicate': duplicate, 'party': p['name'] if p else None,
            'contract': c['title'] if c else None, 'kind': FILE_KINDS.get(r['guess_kind'] or 'boshqa')}


def file_it(actor, inbox_id, party_id, kind, contract_id=None):
    L._need(actor)
    r = q('SELECT * FROM doc_inbox WHERE id=?', (inbox_id,), one=True)
    if not r:
        raise UserError('Hujjat topilmadi.')
    if not party_id:
        raise UserError('Firmani tanlang.')
    if kind not in FILE_KINDS:
        kind = 'boshqa'
    if contract_id and not q('SELECT 1 FROM contracts WHERE id=? AND party_id=?', (contract_id, party_id), one=True):
        contract_id = None
    data = (Path(current_app.config['SURXON'].UPLOAD_DIR) / r['path']).read_bytes()
    fid = L.add_file(actor, party_id, r['name'], data, contract_id, kind)
    if kind == 'kochirma':               # the bank's “payments from the credit account” export → loan use on the credit
        cid = contract_id or next((c['id'] for c in q("SELECT id FROM contracts WHERE party_id=? AND kind='kredit' AND status<>'BEKOR'",
                                                       (party_id,))), None)
        if cid and q("SELECT kind FROM contracts WHERE id=?", (cid,), one=True)['kind'] == 'kredit':
            try:
                L.import_credit_spend(actor, cid, L.parse_credit_export(data))
            except UserError:
                pass
    with tx() as db:
        db.execute('''UPDATE doc_inbox SET status='filed', file_id=?, guess_party_id=?, guess_contract_id=?, guess_kind=?,
                      done_by=?, done_at=? WHERE id=?''', (fid, party_id, contract_id, kind, actor.user_id, now_str(), inbox_id))
    return fid


def reject(actor, inbox_id):
    L._need(actor)
    with tx() as db:
        db.execute("UPDATE doc_inbox SET status='rad', done_by=?, done_at=? WHERE id=? AND status='new'",
                   (actor.user_id, now_str(), inbox_id))
        audit(db, actor, 'VOID', 'doc_inbox', inbox_id)


def pending():
    return q('''SELECT i.*, p.name party_name FROM doc_inbox i LEFT JOIN parties p ON p.id=i.guess_party_id
                WHERE i.status='new' ORDER BY i.id DESC''')


def recent(limit=30):
    return q('''SELECT i.*, p.name party_name, c.title contract_title FROM doc_inbox i LEFT JOIN parties p ON p.id=i.guess_party_id
                LEFT JOIN contracts c ON c.id=i.guess_contract_id WHERE i.status='filed' ORDER BY i.done_at DESC, i.id DESC LIMIT ?''',
             (limit,))


def reply_text(res):
    """One line for the bot / the page about where the document went."""
    if res['duplicate']:
        return f'ℹ️ {res["name"]} avval yuborilgan — ikkinchi marta saqlanmadi.'
    if res['status'] == 'filed':
        return f'✅ {res["name"]} → {res["party"]}' + (f' · {res["contract"]}' if res['contract'] else '') + f' · {res["kind"]}'
    return f'📥 {res["name"]} → Hujjatlar qutisiga tushdi (firmasi aniq emas — saytda bir bosishda ajratiladi).'
