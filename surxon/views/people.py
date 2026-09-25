"""Workers (terimchilar) and their settlement."""
from flask import Blueprint, abort, redirect, render_template, request, url_for

from .. import queries
from ..db import q
from ..photos import read_upload
from ..security import can, perm_required, require
from ..services import create_worker, update_worker
from ..settings import get_float
from ..utils import today_str
from . import PER_PAGE, checkbox, done, page_arg, paginate, post_actor, scope, season_arg

bp = Blueprint('people', __name__)


@bp.route('/ishchilar', methods=['GET', 'POST'])
@perm_required('workers.write', 'reports.view')
def workers():
    if request.method == 'POST':
        require('workers.write')
        wid = create_worker(post_actor(), request.form.get('full_name'), request.form.get('phone', ''),
                            scope() or (int(request.form['brigadier_id']) if request.form.get('brigadier_id', '').isdigit() else None),
                            read_upload(request.files.get('photo')))
        return done('Ishchi qo‘shildi.', request.form.get('next') or url_for('people.worker_detail', worker_id=wid),
                    worker_id=wid)
    year = season_arg()
    term = (request.args.get('q') or '').strip()
    show = request.args.get('show', 'active')
    where = ['1=1'] if show == 'all' else ['w.active=1']
    params = [year, year, today_str()]
    if term:
        where.append('(w.full_name LIKE ? OR w.phone LIKE ?)'); params += [f'%{term}%', f'%{term}%']
    rows = q(f'''SELECT w.*, b.name brigadier_name, p.thumb_path,
                        COALESCE((SELECT SUM(kg) FROM harvests h WHERE h.worker_id=w.id AND h.season_year=? AND h.voided_at IS NULL),0) season_kg,
                        (SELECT COUNT(DISTINCT work_date) FROM harvests h WHERE h.worker_id=w.id AND h.season_year=? AND h.voided_at IS NULL) days,
                        COALESCE((SELECT SUM(kg) FROM harvests h WHERE h.worker_id=w.id AND h.work_date=? AND h.voided_at IS NULL),0) today_kg
                 FROM workers w LEFT JOIN brigadiers b ON b.id=w.brigadier_id LEFT JOIN photos p ON p.id=w.photo_id
                 WHERE {' AND '.join(where)} ORDER BY today_kg DESC, season_kg DESC, w.full_name LIMIT ? OFFSET ?''', params + [PER_PAGE + 1, (page_arg() - 1) * PER_PAGE])
    rows, has_more = paginate(rows, page_arg())
    return render_template('workers.html', rows=rows, term=term, show=show, year=year, page=page_arg(), has_more=has_more,
                           brigadiers=q('SELECT * FROM brigadiers WHERE active=1 ORDER BY name'))


@bp.route('/ishchi/<int:worker_id>', methods=['GET', 'POST'])
@perm_required('workers.write', 'reports.view')
def worker_detail(worker_id):
    w = q('SELECT w.*, p.path photo_path, p.thumb_path FROM workers w LEFT JOIN photos p ON p.id=w.photo_id WHERE w.id=?',
          (worker_id,), one=True)
    if not w:
        abort(404)
    if request.method == 'POST':
        require('workers.write')
        update_worker(post_actor(), worker_id, request.form.get('full_name'), request.form.get('phone', ''),
                      checkbox('active'), read_upload(request.files.get('photo')))
        return done('Ishchi ma’lumoti yangilandi.', url_for('people.worker_detail', worker_id=worker_id))
    year = season_arg()
    days = q('''SELECT h.work_date, SUM(h.kg) kg, COUNT(*) n, GROUP_CONCAT(DISTINCT f.name) fields,
                       GROUP_CONCAT(DISTINCT t.code) trailers
                FROM harvests h LEFT JOIN fields f ON f.id=h.field_id LEFT JOIN equipment t ON t.id=h.trailer_id
                WHERE h.worker_id=? AND h.season_year=? AND h.voided_at IS NULL GROUP BY h.work_date ORDER BY h.work_date DESC''',
             (worker_id, year))
    from ..accounting import worker_balances
    cash = q('''SELECT * FROM cash_entries WHERE worker_id=? AND season_year=? AND voided_at IS NULL ORDER BY entry_date DESC''',
             (worker_id, year))
    total_kg = sum(d['kg'] for d in days)
    bal = next(iter(worker_balances(year, worker_id=worker_id)), None)
    # money only for those allowed to see it; amounts are the frozen per-weighing ones
    show = can('settlement.view')
    return render_template('worker_detail.html', w=w, days=days, rate=None, cash=cash if show else [], total_kg=total_kg,
                           paid=(bal['paid'] + bal['advances']) if bal and show else 0,
                           earned=bal['earned'] if bal and show and bal['earned'] else None, year=year,
                           history=queries.history('worker', worker_id))


@bp.get('/ishchilar/hisob-kitob')
@perm_required('settlement.view')
def settlements():
    # one place for worker money: frozen per-weighing rates, advances, payment orders
    return redirect(url_for('acct.workers', tab='barchasi', season=request.args.get('season')))
