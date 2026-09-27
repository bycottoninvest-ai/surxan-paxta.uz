"""Credits, leasing and contracts: every counterparty with its contracts, files, schedule and payments."""
from flask import Blueprint, abort, current_app, render_template, request, send_from_directory, url_for

from .. import loans as L
from ..db import q
from ..photos import read_upload
from ..security import perm_required, require
from ..utils import UserError, parse_int
from . import done, post_actor

bp = Blueprint('loans', __name__)


@bp.get('/kreditlar')
@perm_required('loans.view')
def home():
    return render_template('loans_home.html', s=L.summary(), up=L.upcoming(90), parties=L.parties_overview(), kinds=L.KINDS,
                           duties=L.obligations(open_only=True, days=60))


@bp.post('/kreditlar/firma')
@perm_required('loans.write')
def party_save():
    pid = L.save_party(post_actor(), parse_int(request.form.get('id'), 'Firma', required=False), name=request.form.get('name'),
                       inn=request.form.get('inn', ''), kind=request.form.get('kind', 'boshqa'), note=request.form.get('note', ''))
    return done('Firma saqlandi.', url_for('loans.party', party_id=pid))


@bp.route('/kreditlar/firma/<int:party_id>', methods=['GET', 'POST'])
@perm_required('loans.view')
def party(party_id):
    p = q('SELECT * FROM parties WHERE id=?', (party_id,), one=True)
    if not p:
        abort(404)
    if request.method == 'POST':
        require('loans.write')
        actor = post_actor()
        act = request.form.get('action')
        if act == 'file':
            n = 0
            for f in request.files.getlist('files'):
                if f and f.filename:
                    L.add_file(actor, party_id, f.filename, f.read(), parse_int(request.form.get('contract_id'), 'Shartnoma', required=False),
                               request.form.get('kind') or 'boshqa')
                    n += 1
            if not n:
                raise UserError('Fayl tanlang.')
            return done(f'{n} ta fayl saqlandi.', url_for('loans.party', party_id=party_id))
        if act == 'move':
            L.add_move(actor, party_id=party_id, contract_id=parse_int(request.form.get('contract_id'), 'Shartnoma', required=False),
                       move_date=request.form.get('move_date'), direction=request.form.get('direction', 'OUT'),
                       amount=request.form.get('amount'), purpose=request.form.get('purpose', ''))
            return done('To‘lov yozildi.', url_for('loans.party', party_id=party_id))
        if act == 'duty':
            L.save_obligation(actor, due_date=request.form.get('due_date'), title=request.form.get('title'), party_id=party_id,
                              contract_id=parse_int(request.form.get('contract_id'), 'Shartnoma', required=False),
                              equipment_id=parse_int(request.form.get('equipment_id'), 'Texnika', required=False),
                              note=request.form.get('note', ''), remind_days=request.form.get('remind_days') or 7)
            return done('Muhim sana qo‘shildi — vaqtida eslatiladi.', url_for('loans.party', party_id=party_id))
        if act in ('duty_done', 'duty_undo'):
            L.done_obligation(actor, parse_int(request.form.get('duty_id'), 'Sana'), undo=act == 'duty_undo')
            return done('Saqlandi.', request.referrer or url_for('loans.party', party_id=party_id))
        if act == 'void_move':
            L.void_move(actor, parse_int(request.form.get('move_id'), 'To‘lov'))
            return done('To‘lov bekor qilindi.', url_for('loans.party', party_id=party_id))
        cid = L.save_contract(actor, None, party_id=party_id, kind=request.form.get('kind'), title=request.form.get('title'),
                              number=request.form.get('number', ''), sign_date=request.form.get('sign_date', ''),
                              start_date=request.form.get('start_date', ''), amount=request.form.get('amount', ''),
                              advance=request.form.get('advance', ''), rate_pct=request.form.get('rate_pct', ''),
                              months=request.form.get('months', ''),
                              equipment_id=parse_int(request.form.get('equipment_id'), 'Texnika', required=False),
                              note=request.form.get('note', ''), end_date=request.form.get('end_date', ''),
                              terms=request.form.get('terms', ''))
        return done('Shartnoma qo‘shildi — endi grafigini yuklang.', url_for('loans.contract', contract_id=cid))
    cs = L.contracts(party_id)
    moves = q('''SELECT m.*, c.title contract_title FROM contract_moves m LEFT JOIN contracts c ON c.id=m.contract_id
                 WHERE m.party_id=? AND m.voided_at IS NULL ORDER BY m.move_date DESC, m.id DESC''', (party_id,))
    files = q('''SELECT f.*, c.title contract_title FROM contract_files f LEFT JOIN contracts c ON c.id=f.contract_id
                 WHERE f.party_id=? ORDER BY f.id DESC''', (party_id,))
    paid = sum(m['amount'] for m in moves if m['direction'] == 'OUT')
    got = sum(m['amount'] for m in moves if m['direction'] == 'IN')
    return render_template('loans_party.html', p=p, cs=cs, moves=moves, files=files, paid=paid, got=got, kinds=L.KINDS,
                           duties=L.obligations(party_id=party_id),
                           ckinds=L.CONTRACT_KINDS, debt=sum(c['st']['debt'] or 0 for c in cs),
                           equipment=q("SELECT id, code, kind FROM equipment WHERE active=1 AND kind<>'telashka' ORDER BY kind, code"))


@bp.route('/kreditlar/shartnoma/<int:contract_id>', methods=['GET', 'POST'])
@perm_required('loans.view')
def contract(contract_id):
    c = q('''SELECT c.*, p.name party_name, p.inn party_inn, e.code equipment_code FROM contracts c JOIN parties p ON p.id=c.party_id
             LEFT JOIN equipment e ON e.id=c.equipment_id WHERE c.id=?''', (contract_id,), one=True)
    if not c:
        abort(404)
    if request.method == 'POST':
        require('loans.write')
        actor = post_actor()
        act = request.form.get('action')
        if act == 'schedule_xlsx':
            f = request.files.get('file')
            if not f or not f.filename:
                raise UserError('Grafik Excel faylini tanlang.')
            data = f.read()
            parsed = L.parse_schedule_xlsx(data)
            n = L.set_schedule(actor, contract_id, parsed['rows'])
            L.add_file(actor, c['party_id'], f.filename, data, contract_id, 'grafik')
            info = parsed['info']
            if info and not c['amount']:        # fill the empty contract fields from the schedule's header
                L.save_contract(actor, contract_id, party_id=c['party_id'], kind=c['kind'], title=c['title'], number=c['number'] or '',
                                sign_date=c['sign_date'] or '', start_date=info.get('start_date') or c['start_date'] or '',
                                amount=info.get('amount') or '', advance=info.get('advance') or '',
                                rate_pct=info.get('rate_pct') or '', months=info.get('months') or '',
                                equipment_id=c['equipment_id'], note=c['note'] or '', end_date=c['end_date'] or '', terms=c['terms'] or '')
            total = sum(r['amount'] for r in parsed['rows'])
            return done(f'Grafik yuklandi: {n} ta to‘lov, jami {total:,.0f} so‘m.'.replace(',', ' '),
                        url_for('loans.contract', contract_id=contract_id))
        if act == 'schedule_auto':
            from datetime import date
            start = L._date(request.form.get('start') or c['start_date'] or c['sign_date'], 'Boshlanish sanasi')
            rows = L.annuity_schedule(L._money(request.form.get('amount') or c['amount'], 'Summa'),
                                      L._money(request.form.get('rate_pct') or c['rate_pct'] or 0, 'Foiz'),
                                      int(L._money(request.form.get('months') or c['months'], 'Muddat')),
                                      date.fromisoformat(start), int(request.form.get('every') or 1))
            n = L.set_schedule(actor, contract_id, rows)
            return done(f'Grafik tuzildi: {n} ta to‘lov.', url_for('loans.contract', contract_id=contract_id))
        if act == 'schedule_one':
            n = L.set_schedule(actor, contract_id, [{'due_date': L._date(request.form.get('due_date'), 'To‘lov sanasi'),
                                                    'principal': L._money(request.form.get('amount'), 'Summa'), 'interest': 0,
                                                    'amount': L._money(request.form.get('amount'), 'Summa')}], replace=False)
            return done('To‘lov grafikka qo‘shildi.', url_for('loans.contract', contract_id=contract_id))
        if act == 'terms':
            L.save_contract(actor, contract_id, party_id=c['party_id'], kind=c['kind'], title=c['title'], number=c['number'] or '',
                            sign_date=c['sign_date'] or '', start_date=c['start_date'] or '', amount=c['amount'] or '',
                            advance=c['advance'] or '', rate_pct=c['rate_pct'] or '', months=c['months'] or '',
                            equipment_id=c['equipment_id'], note=c['note'] or '', status=c['status'],
                            end_date=request.form.get('end_date') or c['end_date'] or '', terms=request.form.get('terms', ''))
            return done('Shartlar saqlandi.', url_for('loans.contract', contract_id=contract_id))
        if act == 'status':
            L.save_contract(actor, contract_id, party_id=c['party_id'], kind=c['kind'], title=c['title'], number=c['number'] or '',
                            sign_date=c['sign_date'] or '', start_date=c['start_date'] or '', amount=c['amount'] or '',
                            advance=c['advance'] or '', rate_pct=c['rate_pct'] or '', months=c['months'] or '',
                            equipment_id=c['equipment_id'], note=c['note'] or '', status=request.form.get('status'),
                            end_date=c['end_date'] or '', terms=c['terms'] or '')
            return done('Holat saqlandi.', url_for('loans.contract', contract_id=contract_id))
        raise UserError('Noma’lum amal.')
    st = L.contract_state(c)
    moves = q('SELECT * FROM contract_moves WHERE contract_id=? AND voided_at IS NULL ORDER BY move_date DESC, id DESC', (contract_id,))
    files = q('SELECT * FROM contract_files WHERE contract_id=? ORDER BY id DESC', (contract_id,))
    return render_template('loans_contract.html', c=c, st=st, moves=moves, files=files, ckinds=L.CONTRACT_KINDS,
                           duties=L.obligations(contract_id=contract_id))


@bp.get('/kreditlar/fayl/<int:file_id>')
@perm_required('loans.view')
def file(file_id):
    f = q('SELECT * FROM contract_files WHERE id=?', (file_id,), one=True)
    if not f:
        abort(404)
    return send_from_directory(current_app.config['SURXON'].UPLOAD_DIR, f['path'], download_name=f['name'],
                               as_attachment=request.args.get('download') == '1')
