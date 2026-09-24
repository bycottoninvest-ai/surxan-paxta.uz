"""Reports (on screen, print/PDF, Excel) and the audit trail."""
from flask import Blueprint, abort, g, render_template, request, send_file

from .. import queries
from ..db import q
from ..exports import to_xlsx
from ..security import can, perm_required
from ..settings import get_float
from ..utils import parse_date, today_str
from . import PER_PAGE, page_arg, paginate, scope, season_arg

bp = Blueprint('reports', __name__)

K, M, T, D, N = 'kg', 'money', 'text', 'date', 'num'


def _daily(year, args):
    day = parse_date(args.get('date') or today_str())
    brig = scope()
    rows = q('''SELECT tl.id, t.code trailer, tr.code tractor, f.name field, b.name brigadier, tl.status,
                       tl.full_at, tl.internal_kg, w.gross_kg, w.tare_kg, w.net_kg, w.diff_kg, wb.number waybill,
                       nr.accepted_kg
                FROM trailer_loads tl JOIN equipment t ON t.id=tl.trailer_id LEFT JOIN equipment tr ON tr.id=tl.tractor_id
                LEFT JOIN fields f ON f.id=tl.field_id LEFT JOIN brigadiers b ON b.id=tl.brigadier_id
                LEFT JOIN weighings w ON w.load_id=tl.id LEFT JOIN waybills wb ON wb.load_id=tl.id
                LEFT JOIN nayman_receipts nr ON nr.waybill_id=wb.id
                WHERE tl.season_year=? AND tl.load_date=? AND tl.status<>'BEKOR' ''' + (' AND tl.brigadier_id=?' if brig else '') +
             ' ORDER BY tl.id', (year, day) + ((brig,) if brig else ()))
    return {'title': f'Kunlik hisobot — {day}', 'filters': ['date'], 'date': day,
            'columns': [('id', '№', N), ('trailer', 'Telashka', T), ('tractor', 'Traktor', T), ('field', 'Dala', T),
                        ('brigadier', 'Brigadir', T), ('status', 'Holat', T), ('full_at', 'TOLDI vaqti', D),
                        ('internal_kg', 'Ichki kg', K), ('gross_kg', 'Brutto', K), ('tare_kg', 'Tara', K),
                        ('net_kg', 'Netto', K), ('diff_kg', 'Farq', K), ('waybill', 'Nakladnoy', T),
                        ('accepted_kg', 'Nayman qabul', K)],
            'rows': [dict(r) for r in rows], 'sum': ['internal_kg', 'net_kg', 'accepted_kg']}


def _waybills(year, args):
    rows = queries.waybills(year, brig=scope(), limit=100000)
    return {'title': f'Nakladnoylar reestri — {year}',
            'columns': [('number', 'Nakladnoy', T), ('document_date', 'Sana', D), ('trailer_code', 'Telashka', T),
                        ('tractor_code', 'Traktor', T), ('vehicle_plate', 'Mashina', T), ('field_name', 'Dala', T),
                        ('brigadier_name', 'Brigadir', T), ('gross_kg', 'Brutto', K), ('tare_kg', 'Tara', K),
                        ('net_kg', 'Netto', K), ('accepted_kg', 'Nayman qabul', K), ('nayman_diff_kg', 'Farq', K),
                        ('nayman_diff_reason', 'Farq sababi', T), ('status', 'Holat', T)],
            'rows': [dict(r) for r in rows], 'sum': ['net_kg', 'accepted_kg', 'nayman_diff_kg']}


def _workers(year, args):
    brig = scope()
    rows = q('''SELECT w.full_name, b.name brigadier, COUNT(DISTINCT h.work_date) days, SUM(h.kg) kg,
                       ROUND(SUM(h.kg)*1.0/COUNT(DISTINCT h.work_date),1) avg_kg, MAX(h.work_date) last_day
                FROM harvests h JOIN workers w ON w.id=h.worker_id LEFT JOIN brigadiers b ON b.id=h.brigadier_id
                WHERE h.season_year=? AND h.method='hand' AND h.voided_at IS NULL''' + (' AND h.brigadier_id=?' if brig else '') +
             ' GROUP BY w.id ORDER BY kg DESC', (year,) + ((brig,) if brig else ()))
    rate = get_float('worker_rate_hand', None)
    out = []
    for r in rows:
        d = dict(r)
        if rate and can('settlement.view'):
            d['earned'] = round(d['kg'] * rate)
        out.append(d)
    cols = [('full_name', 'F.I.Sh.', T), ('brigadier', 'Brigadir', T), ('days', 'Kunlar', N), ('kg', 'Jami kg', K),
            ('avg_kg', 'O‘rtacha kg/kun', K), ('last_day', 'Oxirgi kun', D)]
    if rate and can('settlement.view'):
        cols.append(('earned', f'Ish haqi ({rate:g} so‘m/kg)', M))
    return {'title': f'Terimchilar hisoboti — {year}', 'columns': cols, 'rows': out, 'sum': ['kg', 'earned']}


def _combines(year, args):
    brig = scope()
    rows = q('''SELECT c.code, c.operator_name, COUNT(DISTINCT h.work_date) days, COUNT(*) n, SUM(h.kg) kg,
                       MAX(h.work_date) last_day
                FROM harvests h JOIN equipment c ON c.id=h.combine_id
                WHERE h.season_year=? AND h.method='combine' AND h.voided_at IS NULL''' + (' AND h.brigadier_id=?' if brig else '') +
             ' GROUP BY c.id ORDER BY kg DESC', (year,) + ((brig,) if brig else ()))
    rate = get_float('combine_rate', None)
    out = []
    for r in rows:
        d = dict(r)
        if rate and can('settlement.view'):
            d['earned'] = round(d['kg'] * rate)
        out.append(d)
    cols = [('code', 'Kombayn', T), ('operator_name', 'Kombaynchi', T), ('days', 'Kunlar', N), ('n', 'Yozuvlar', N),
            ('kg', 'Jami kg (ichki)', K), ('last_day', 'Oxirgi kun', D)]
    if rate and can('settlement.view'):
        cols.append(('earned', f'Kombayn haqi ({rate:g} so‘m/kg)', M))
    return {'title': f'Kombaynlar hisoboti — {year}', 'columns': cols, 'rows': out, 'sum': ['n', 'kg', 'earned']}


def _brigadiers(year, args):
    rows = []
    for r in queries.brigadier_results(year):
        d = dict(r)
        d['kg_ha'] = round(d['net_kg'] / d['area_ha']) if d['area_ha'] else None
        rows.append(d)
    return {'title': f'Brigadirlar bo‘yicha — {year}',
            'columns': [('name', 'Brigadir', T), ('area_ha', 'Maydon (ga)', N), ('hand_kg', 'Qo‘l terimi (ichki)', K),
                        ('combine_kg', 'Kombayn (ichki)', K), ('net_kg', 'Tarozi netto (yakuniy)', K),
                        ('kg_ha', 'kg/ga', K)],
            'rows': rows, 'sum': ['area_ha', 'hand_kg', 'combine_kg', 'net_kg']}


def _fields(year, args):
    rows = []
    for r in queries.field_yields(year, scope()):
        d = dict(r)
        d['kg_ha'] = round(d['net_kg'] / d['area_ha']) if d['area_ha'] else None
        rows.append(d)
    return {'title': f'Dala va hosildorlik — {year}',
            'columns': [('code', 'Kod', T), ('name', 'Dala', T), ('brigadier_name', 'Brigadir', T), ('area_ha', 'Ga', N),
                        ('internal_kg', 'Ichki kg', K), ('net_kg', 'Tarozi netto', K), ('kg_ha', 'kg/ga', K)],
            'rows': rows, 'sum': ['area_ha', 'internal_kg', 'net_kg']}


def _nayman(year, args):
    rows = [dict(r) for r in queries.waybills(year, brig=scope(), limit=100000) if r['status'] != 'BEKOR']
    for r in rows:
        r['pending'] = 'Kutilmoqda' if r['accepted_kg'] is None else ''
    return {'title': f'Nayman sverka (jo‘natilgan / qabul qilingan) — {year}',
            'columns': [('number', 'Nakladnoy', T), ('document_date', 'Jo‘natilgan', D), ('net_kg', 'Jo‘natilgan kg', K),
                        ('received_date', 'Qabul sanasi', D), ('accepted_kg', 'Qabul kg', K), ('nayman_diff_kg', 'Farq', K),
                        ('nayman_diff_reason', 'Sabab', T), ('pending', 'Holat', T)],
            'rows': rows, 'sum': ['net_kg', 'accepted_kg', 'nayman_diff_kg']}


def _payments(year, args):
    rows = q('''SELECT p.payment_date, p.amount, p.method, p.payer, wb.number, p.note,
                       CASE WHEN p.voided_at IS NOT NULL THEN 'BEKOR: '||p.void_reason ELSE '' END status
                FROM payments p LEFT JOIN waybills wb ON wb.id=p.waybill_id WHERE p.season_year=? ORDER BY p.payment_date''', (year,))
    return {'title': f'To‘lovlar hisoboti — {year}', 'finance': True,
            'columns': [('payment_date', 'Sana', D), ('amount', 'Summa', M), ('method', 'Usul', T), ('payer', 'To‘lovchi', T),
                        ('number', 'Nakladnoy', T), ('note', 'Izoh', T), ('status', 'Holat', T)],
            'rows': [dict(r) for r in rows], 'sum': ['amount'], 'sum_skip': lambda r: bool(r['status'])}


def _expenses(year, args):
    rows = q('''SELECT e.expense_date, e.category, e.amount, f.name field, b.name brigadier, eq.code equipment, e.payer, e.note,
                       CASE WHEN e.cash_entry_id IS NOT NULL THEN 'Kassadan' ELSE '' END src,
                       CASE WHEN e.voided_at IS NOT NULL THEN 'BEKOR: '||e.void_reason ELSE '' END status
                FROM expenses e LEFT JOIN fields f ON f.id=e.field_id LEFT JOIN brigadiers b ON b.id=e.brigadier_id
                LEFT JOIN equipment eq ON eq.id=e.equipment_id WHERE e.season_year=? ORDER BY e.expense_date''', (year,))
    return {'title': f'Xarajatlar hisoboti — {year}', 'finance': True,
            'columns': [('expense_date', 'Sana', D), ('category', 'Turi', T), ('amount', 'Summa', M), ('field', 'Dala', T),
                        ('brigadier', 'Brigadir', T), ('equipment', 'Texnika', T), ('payer', 'Kimga', T), ('src', 'Manba', T),
                        ('note', 'Izoh', T), ('status', 'Holat', T)],
            'rows': [dict(r) for r in rows], 'sum': ['amount'], 'sum_skip': lambda r: bool(r['status'])}


def _cash(year, args):
    from ..services import CASH_CATEGORIES
    rows = q('''SELECT c.entry_date, c.direction, c.category, c.amount, w.full_name worker, c.counterparty, c.note,
                       c.voided_at, c.void_reason
                FROM cash_entries c LEFT JOIN workers w ON w.id=c.worker_id WHERE c.season_year=? ORDER BY c.entry_date, c.id''',
             (year,))
    out, bal = [], 0
    for r in rows:
        d = dict(r)
        d['category'] = CASH_CATEGORIES.get(d['category'], ('', d['category']))[1]
        d['inflow'] = d['amount'] if d['direction'] == 'IN' else None
        d['outflow'] = d['amount'] if d['direction'] == 'OUT' else None
        if not d['voided_at']:
            bal += d['amount'] if d['direction'] == 'IN' else -d['amount']
        d['balance'] = bal
        d['status'] = f"BEKOR: {d['void_reason']}" if d['voided_at'] else ''
        out.append(d)
    return {'title': f'Asadbek kassasi — {year}', 'finance': True,
            'columns': [('entry_date', 'Sana', D), ('category', 'Operatsiya', T), ('inflow', 'Kirim', M), ('outflow', 'Chiqim', M),
                        ('balance', 'Qoldiq', M), ('worker', 'Ishchi', T), ('counterparty', 'Kontragent', T),
                        ('note', 'Izoh', T), ('status', 'Holat', T)],
            'rows': out, 'sum': ['inflow', 'outflow'], 'sum_skip': lambda r: bool(r['status'])}


def _trailers(year, args):
    rows = q('''SELECT t.code, COUNT(tl.id) trips, SUM(CASE WHEN tl.status='TORTILDI' THEN 1 ELSE 0 END) weighed,
                       COALESCE(SUM(w.net_kg),0) net_kg, ROUND(AVG(w.net_kg)) avg_net, COALESCE(SUM(tl.internal_kg),0) internal_kg
                FROM equipment t LEFT JOIN trailer_loads tl ON tl.trailer_id=t.id AND tl.season_year=? AND tl.status<>'BEKOR'
                LEFT JOIN weighings w ON w.load_id=tl.id AND w.status='YAKUNLANDI'
                WHERE t.kind='telashka' GROUP BY t.id ORDER BY t.code''', (year,))
    return {'title': f'Telashkalar bo‘yicha — {year}',
            'columns': [('code', 'Telashka', T), ('trips', 'Reyslar', N), ('weighed', 'Tortilgan', N),
                        ('internal_kg', 'Ichki kg', K), ('net_kg', 'Netto jami', K), ('avg_net', 'O‘rtacha netto', K)],
            'rows': [dict(r) for r in rows], 'sum': ['trips', 'weighed', 'internal_kg', 'net_kg']}


def _seasons(year, args):
    rows = []
    for r in queries.season_comparison():
        d = dict(r)
        d['yield'] = round(d['net_kg'] / d['area_now']) if d['area_now'] else None
        rows.append(d)
    cols = [('year', 'Mavsum', T), ('status', 'Holat', T), ('net_kg', 'Tarozi netto', K), ('hand_kg', 'Qo‘l terimi', K),
            ('combine_kg', 'Kombayn', K), ('shipped_kg', 'Jo‘natilgan', K), ('accepted_kg', 'Qabul qilingan', K),
            ('yield', 'kg/ga (joriy maydon)', K), ('waybill_count', 'Nakladnoylar', N), ('photo_count', 'Rasmlar', N)]
    if can('reports.finance'):
        cols += [('expenses', 'Xarajatlar', M), ('received', 'Tushum', M)]
    return {'title': 'Mavsumlar solishtirmasi', 'columns': cols, 'rows': rows, 'sum': []}


REPORTS = {
    'kunlik': ('Kunlik hisobot', 'calendar', _daily, 'reports.view'),
    'nakladnoylar': ('Nakladnoylar reestri', 'doc', _waybills, 'reports.view'),
    'terimchilar': ('Terimchilar hisoboti', 'users', _workers, 'reports.view'),
    'kombaynlar': ('Kombaynlar hisoboti', 'combine', _combines, 'reports.view'),
    'brigadirlar': ('Brigadirlar bo‘yicha', 'user', _brigadiers, 'reports.view'),
    'dalalar': ('Dala va hosildorlik', 'map', _fields, 'reports.view'),
    'telashkalar': ('Telashkalar bo‘yicha', 'trailer', _trailers, 'reports.view'),
    'nayman': ('Nayman sverka', 'factory', _nayman, 'reports.view'),
    'tolovlar': ('To‘lovlar hisoboti', 'wallet', _payments, 'reports.finance'),
    'xarajatlar': ('Xarajatlar hisoboti', 'receipt', _expenses, 'reports.finance'),
    'kassa': ('Kassa hisoboti', 'cash', _cash, 'reports.finance'),
    'mavsumlar': ('Mavsumlar solishtirmasi', 'chart', _seasons, 'reports.view'),
}


@bp.get('/hisobotlar')
@perm_required('reports.view')
def index():
    year = season_arg()
    items = [(k, v[0], v[1]) for k, v in REPORTS.items() if can(v[3])]
    return render_template('reports.html', items=items, year=year, seasons_cmp=queries.season_comparison())


@bp.get('/hisobot/<key>')
@perm_required('reports.view')
def report(key):
    if key not in REPORTS:
        abort(404)
    label, _icon, fn, perm = REPORTS[key]
    if not can(perm):
        abort(403)
    year = season_arg()
    spec = fn(year, request.args)
    skip = spec.get('sum_skip') or (lambda r: False)
    spec['totals'] = {c: sum((r.get(c) or 0) for r in spec['rows'] if not skip(r)) for c in spec.get('sum', [])}
    if request.args.get('format') == 'xlsx':
        buf = to_xlsx(spec)
        return send_file(buf, as_attachment=True, download_name=f'surxon_{key}_{year}.xlsx',
                         mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    return render_template('report.html', spec=spec, key=key, year=year, printing=bool(request.args.get('print')))


@bp.get('/audit')
@perm_required('audit.view')
def audit_log():
    f = request.args
    where, params = ['1=1'], []
    if f.get('entity'):
        where.append('a.entity_type=?'); params.append(f['entity'])
    if f.get('id'):
        where.append('a.entity_id=?'); params.append(f['id'])
    if f.get('user', '').isdigit():
        where.append('a.user_id=?'); params.append(int(f['user']))
    if f.get('action'):
        where.append('a.action=?'); params.append(f['action'])
    if f.get('date'):
        where.append('substr(a.created_at,1,10)=?'); params.append(f['date'])
    rows = q(f'''SELECT a.*, u.full_name, u.role FROM audit_logs a LEFT JOIN users u ON u.id=a.user_id
                 WHERE {' AND '.join(where)} ORDER BY a.id DESC LIMIT ? OFFSET ?''',
             params + [PER_PAGE * 2 + 1, (page_arg() - 1) * PER_PAGE * 2])
    rows, has_more = paginate(rows, page_arg(), PER_PAGE * 2)
    return render_template('audit.html', rows=rows, page=page_arg(), has_more=has_more, users=q('SELECT id, full_name FROM users ORDER BY full_name'),
                           entities=[r[0] for r in q('SELECT DISTINCT entity_type FROM audit_logs ORDER BY 1')],
                           actions=[r[0] for r in q('SELECT DISTINCT action FROM audit_logs ORDER BY 1')])
