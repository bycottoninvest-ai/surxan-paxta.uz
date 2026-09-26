"""Buxgalteriya: payments from Nayman, Asadbek cash desk, expenses."""
from flask import Blueprint, render_template, request, url_for

from .. import queries
from ..db import q
from ..photos import read_upload
from ..security import perm_required, require
from ..services import (CASH_CATEGORIES, EXPENSE_CATEGORIES, add_cash_entry, add_expense, add_payment, cash_balance,
                        expected_payment, payment_rule, void_cash_entry, void_expense, void_payment)
from ..utils import parse_date, parse_int, parse_money, today_str
from . import checkbox, done, form_uuid, post_actor, season_arg

bp = Blueprint('finance', __name__)


@bp.route('/tolovlar', methods=['GET', 'POST'])
@perm_required('payments.view')
def payments():
    if request.method == 'POST':
        require('payments.write')
        pid = add_payment(post_actor(), amount=parse_money(request.form.get('amount')),
                          payment_date=parse_date(request.form.get('payment_date'), 'To‘lov sanasi'),
                          waybill_id=parse_int(request.form.get('waybill_id'), 'Nakladnoy', required=False),
                          method=request.form.get('method', ''), payer=request.form.get('payer') or 'Nayman',
                          note=request.form.get('note', ''), client_uuid=form_uuid(), to_cash=checkbox('to_cash'),
                          photo=read_upload(request.files.get('photo')))
        return done(f'To‘lov #{pid} saqlandi.', url_for('finance.payments'))
    year = season_arg()
    rows = q('''SELECT p.*, wb.number waybill_number, u.full_name created_name FROM payments p
                LEFT JOIN waybills wb ON wb.id=p.waybill_id LEFT JOIN users u ON u.id=p.created_by
                WHERE p.season_year=? ORDER BY p.payment_date DESC, p.id DESC''', (year,))
    open_wb = q('''SELECT wb.id, wb.number, wb.net_kg, nr.accepted_kg, nr.amount, nr.received_date,
                          (SELECT COALESCE(SUM(amount),0) FROM payments p WHERE p.waybill_id=wb.id AND p.voided_at IS NULL) paid
                   FROM waybills wb LEFT JOIN nayman_receipts nr ON nr.waybill_id=wb.id
                   WHERE wb.season_year=? AND wb.status<>'BEKOR' ORDER BY wb.seq DESC LIMIT 300''', (year,))
    from ..pricing import money
    m = money(year)
    wb_info = []
    for w in open_wb:
        amount = m.get(w['id'], {}).get('amount')
        exp = expected_payment(w['received_date'], amount) if amount else None
        wb_info.append({**dict(w), 'amount': amount, 'price': m.get(w['id'], {}).get('price'), 'expected': exp,
                        'remaining': (amount - w['paid']) if amount else None})
    return render_template('payments.html', rows=rows, waybills=wb_info, year=year, rule=payment_rule(),
                           fin=queries.finance_summary(year))


@bp.post('/tolov/<int:payment_id>/bekor')
@perm_required('payments.write')
def payment_void(payment_id):
    void_payment(post_actor(), payment_id, request.form.get('reason'))
    return done('To‘lov bekor qilindi.', url_for('finance.payments'))


@bp.route('/kassa', methods=['GET', 'POST'])
@perm_required('cash.view')
def cash():
    if request.method == 'POST':
        require('cash.write')
        add_cash_entry(post_actor(), category=request.form.get('category'), amount=parse_money(request.form.get('amount')),
                       entry_date=parse_date(request.form.get('entry_date'), 'Sana'),
                       worker_id=parse_int(request.form.get('worker_id'), 'Ishchi', required=False),
                       counterparty=request.form.get('counterparty', ''), note=request.form.get('note', ''),
                       client_uuid=form_uuid(), photo=read_upload(request.files.get('photo')))
        return done('Kassa yozuvi saqlandi.', url_for('finance.cash'))
    year = season_arg()
    rows = q('''SELECT c.*, w.full_name worker_name, u.full_name created_name FROM cash_entries c
                LEFT JOIN workers w ON w.id=c.worker_id LEFT JOIN users u ON u.id=c.created_by
                WHERE c.season_year=? ORDER BY c.entry_date DESC, c.id DESC''', (year,))
    running, bal = {}, 0
    for r in sorted(rows, key=lambda r: (r['entry_date'], r['id'])):
        if not r['voided_at']:
            bal += r['amount'] if r['direction'] == 'IN' else -r['amount']
        running[r['id']] = bal
    has_opening = any(r['category'] == 'opening' and not r['voided_at'] for r in rows)
    from ..db import get_db
    return render_template('cash.html', rows=rows, running=running, balance=cash_balance(get_db(), year), year=year,
                           categories=CASH_CATEGORIES, has_opening=has_opening, fin=queries.finance_summary(year),
                           workers=q('SELECT id, full_name FROM workers WHERE active=1 ORDER BY full_name'))


@bp.post('/kassa/<int:entry_id>/bekor')
@perm_required('cash.write')
def cash_void(entry_id):
    void_cash_entry(post_actor(), entry_id, request.form.get('reason'))
    return done('Kassa yozuvi bekor qilindi.', url_for('finance.cash'))


@bp.route('/xarajatlar', methods=['GET', 'POST'])
@perm_required('expenses.write', 'reports.finance')
def expenses():
    if request.method == 'POST':
        require('expenses.write')
        add_expense(post_actor(), amount=parse_money(request.form.get('amount')),
                    expense_date=parse_date(request.form.get('expense_date'), 'Sana'),
                    category=request.form.get('category'),
                    field_id=parse_int(request.form.get('field_id'), 'Dala', required=False),
                    brigadier_id=parse_int(request.form.get('brigadier_id'), 'Brigadir', required=False),
                    equipment_id=parse_int(request.form.get('equipment_id'), 'Texnika', required=False),
                    payer=request.form.get('payer', ''), note=request.form.get('note', ''),
                    from_cash=checkbox('from_cash'), client_uuid=form_uuid(), photo=read_upload(request.files.get('photo')))
        return done('Xarajat saqlandi.', url_for('finance.expenses'))
    year = season_arg()
    rows = q('''SELECT e.*, f.code field_code, b.name brigadier_name, eq.code equipment_code, u.full_name created_name
                FROM expenses e LEFT JOIN fields f ON f.id=e.field_id LEFT JOIN brigadiers b ON b.id=e.brigadier_id
                LEFT JOIN equipment eq ON eq.id=e.equipment_id LEFT JOIN users u ON u.id=e.created_by
                WHERE e.season_year=? ORDER BY e.expense_date DESC, e.id DESC''', (year,))
    by_cat = q('''SELECT category, SUM(amount) total, COUNT(*) n FROM expenses WHERE season_year=? AND voided_at IS NULL
                  GROUP BY category ORDER BY total DESC''', (year,))
    return render_template('expenses.html', rows=rows, by_cat=by_cat, year=year, categories=EXPENSE_CATEGORIES,
                           fields=q('SELECT * FROM fields WHERE active=1 ORDER BY code'),
                           brigadiers=q('SELECT * FROM brigadiers WHERE active=1 ORDER BY name'),
                           equipment=q('SELECT * FROM equipment WHERE active=1 ORDER BY kind, code'), today=today_str())


@bp.post('/xarajat/<int:expense_id>/bekor')
@perm_required('expenses.approve')
def expense_void(expense_id):
    void_expense(post_actor(), expense_id, request.form.get('reason'))
    return done('Xarajat bekor qilindi.', url_for('finance.expenses'))
