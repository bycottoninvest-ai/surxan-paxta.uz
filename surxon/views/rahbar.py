"""Director's phone panel (read-only): six cards and their details. No button here changes money, kg or status.

Figures come from surxon.director, which reads the same saved operations the computer screens use. The page polls a
tiny endpoint (the latest audit id) and reloads only when something new was saved; without a connection it keeps the
last figures and says they are old.
"""
from flask import Blueprint, abort, jsonify, render_template, request

from .. import director as D
from ..db import get_db, q
from ..security import perm_required
from ..settings import get_float
from ..utils import now_str
from . import season_arg

bp = Blueprint('rahbar', __name__)
VIEW = 'reports.finance'


def _period():
    year = season_arg()
    p, a, b, label = D.period(request.args.get('period', 'bugun'), year)
    return year, p, a, b, label


def _ctx(**kw):
    year, p, a, b, label = _period()
    return dict(year=year, period=p, date_from=a, date_to=b, period_label=label, periods=D.PERIODS,
                stamp=D.stamp(), loaded_at=now_str(), warn_pct=get_float('punkt_alert_pct', 3) or 3, **kw)


@bp.get('/rahbar')
@perm_required(VIEW)
def home():
    year, p, a, b, _ = _period()
    checks = D.checks(year, a, b)
    return render_template('rahbar_home.html', **_ctx(
        cot=D.cotton(year, a, b), now=D.trips_now(), cash=D.cash(a, b), wages=D.wages(year, a, b), fuel=D.fuel(a, b),
        checks=checks, red=sum(1 for c in checks if c['level'] == 'red'), feed=D.feed(8), staff=_staff_count(),
        fleet=_fleet_count()))


def _fleet_count():
    from .. import fleet
    from ..utils import today_str
    rows = fleet.live()
    day = today_str()
    return {'n': len(rows), 'work': sum(1 for r in rows if r['state'] in ('field', 'road')),
            'alerts': sum(1 for r in rows if r['alert']), 'today': fleet.works(day, day)[:5] if rows else []}


def _staff_count():
    from .. import staffmap
    ppl = staffmap.people()
    return {'n': len(ppl), 'live': sum(1 for x in ppl if not x['stale'])}


@bp.get('/rahbar/xodimlar')
@perm_required(VIEW)
def staff_map():
    from .. import staffmap
    from ..settings import get_setting
    c = (get_setting('map_center') or '42.3,59.6').split(',')
    return render_template('rahbar_staff.html', **_ctx(people=staffmap.people(), center=[float(c[0]), float(c[1])]))


@bp.get('/rahbar/xodimlar.json')
@perm_required(VIEW)
def staff_json():
    from .. import staffmap
    if request.args.get('iz'):
        return jsonify(track=staffmap.track(request.args['iz']))
    return jsonify(people=staffmap.people(), at=now_str()[11:19])


@bp.get('/rahbar/texnika')
@perm_required(VIEW)
def fleet_map():
    from .. import fleet
    from ..settings import get_setting
    year, p, a, b, _ = _period()
    c = (get_setting('map_center') or '42.3,59.6').split(',')
    import json
    from .. import geo
    works = fleet.works(a, b)
    polys = {}
    for r in q('SELECT id, code, polygon_json FROM fields WHERE polygon_json IS NOT NULL AND active=1'):
        try:
            polys[r['id']] = (r['code'], json.loads(r['polygon_json']))
        except ValueError:
            pass
    for w in works:                                   # the covered grid cells as small squares for the map
        poly = polys.get(w['field_id'], (None, None))[1]
        ids = set(w.pop('cells'))
        w['polys'] = [c['poly'] for c in geo.field_grid(poly)[0] if c['id'] in ids] if poly else []
    return render_template('rahbar_fleet.html', **_ctx(machines=fleet.live(), center=[float(c[0]), float(c[1])],
                                                        usage=fleet.usage(a, b), works=works,
                                                        fields=[{'code': v[0], 'poly': v[1]} for v in polys.values()]))


@bp.get('/rahbar/texnika.json')
@perm_required(VIEW)
def fleet_json():
    from .. import fleet
    if request.args.get('iz', '').isdigit():
        return jsonify(track=fleet.track(int(request.args['iz'])))
    return jsonify(machines=fleet.live(), at=now_str()[11:19])


@bp.get('/rahbar/avatar/<path:rel>')
@perm_required(VIEW)
def avatar(rel):
    from flask import current_app, send_from_directory
    if not rel.startswith('avatars/') or '..' in rel:
        abort(404)
    return send_from_directory(current_app.config['SURXON'].UPLOAD_DIR, rel, max_age=86400)


@bp.get('/rahbar/holat')
@perm_required(VIEW)
def state():
    return jsonify(ok=True, stamp=D.stamp(), at=now_str()[11:19])


@bp.get('/rahbar/paxta')
@perm_required(VIEW)
def cotton():
    year, p, a, b, _ = _period()
    tab = request.args.get('tab', 'dalalar')
    from ..reporting import brigade_summary
    data = {'dalalar': lambda: D.by_field(year, a, b), 'brigadalar': lambda: brigade_summary(a, b),
            'terimchilar': lambda: D.by_worker(year, a, b), 'reyslar': lambda: D.period_trips(year, a, b)}
    tab = tab if tab in data else 'dalalar'
    return render_template('rahbar_cotton.html', **_ctx(tab=tab, rows=data[tab](), cot=D.cotton(year, a, b), now=D.trips_now()))


@bp.get('/rahbar/yolda')
@perm_required(VIEW)
def moving():
    from ..transit import on_the_way
    return render_template('rahbar_moving.html', **_ctx(rows=D.moving_trips(), now=D.trips_now(),
                                                        etas={d['lid']: d for d in on_the_way() if d['eta']}))


@bp.get('/rahbar/reys/<int:load_id>')
@perm_required(VIEW)
def trip(load_id):
    t = D.trip(load_id)
    if not t:
        abort(404)
    from .. import queries
    from .main import can_see_photo
    photos = [p for p in queries.photos_for(load_id=load_id) if can_see_photo(p)]
    return render_template('rahbar_trip.html', **_ctx(t=t, photos=photos, timeline=queries.load_timeline(load_id),
                                                      harvest=q('''SELECT COUNT(*) n, COUNT(DISTINCT worker_id) people,
                                                                   COALESCE(SUM(kg),0) kg FROM harvests
                                                                   WHERE load_id=? AND voided_at IS NULL''', (load_id,), one=True)))


@bp.get('/rahbar/kassa')
@perm_required(VIEW)
def cash():
    year, p, a, b, _ = _period()
    return render_template('rahbar_cash.html', **_ctx(cash=D.cash(a, b), ops=D.cash_ops(a, b)))


@bp.get('/rahbar/ish-haqi')
@perm_required(VIEW)
def wages():
    year, p, a, b, _ = _period()
    w = D.wages(year, a, b)
    owed = sorted((r for r in w['rows'] if r['balance'] > 0), key=lambda r: -r['balance'])[:60]
    return render_template('rahbar_wages.html', **_ctx(w=w, owed=owed))


@bp.get('/rahbar/ishchi/<int:worker_id>')
@perm_required(VIEW)
def worker(worker_id):
    from .. import accounting as A
    from ..wallet import worker_figures
    year = season_arg()
    f = worker_figures(get_db(), year, worker_id)
    harvests, money, _ = A.worker_history(worker_id, year)
    return render_template('rahbar_worker.html', **_ctx(f=f, harvests=harvests[:40], money=money[:40]))


@bp.get('/rahbar/solyarka')
@perm_required(VIEW)
def fuel():
    year, p, a, b, _ = _period()
    from .. import fuel as F
    return render_template('rahbar_fuel.html', **_ctx(f=D.fuel(a, b), ops=F.ops(a, b, 60), machines=F.by_machine(a, b)))


@bp.get('/rahbar/tekshirish')
@perm_required(VIEW)
def checks():
    year, p, a, b, _ = _period()
    return render_template('rahbar_checks.html', **_ctx(items=D.checks(year, a, b)))
