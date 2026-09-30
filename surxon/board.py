"""“Bugungi paxta” — the main screen in one look: the cotton's way from the field to the punkt (picked → closed → sent →
arrived → accepted), today's combines and hand picking, the punkt's state (with the cluster's PQ-17: moisture, dirt,
deduction), fields, transport, fuel, cash and the brigades. Only reads; every number comes from the same tables as
the rest of the system.
"""
from .db import q, scalar

LIVE = 'h.voided_at IS NULL'


def _photo(pid):
    if not pid:
        return None
    r = q('SELECT thumb_path FROM photos WHERE id=? AND voided_at IS NULL', (pid,), one=True)
    return r['thumb_path'] if r else None


def build(year, dan, gacha, brig=None, money=False):
    bf, bp = (' AND h.brigadier_id=?', (brig,)) if brig else ('', ())
    lf = ' AND tl.brigadier_id=?' if brig else ''
    per = (dan, gacha)

    harvest = q(f'''SELECT COALESCE(SUM(kg),0) total, COALESCE(SUM(CASE WHEN method='hand' THEN kg END),0) hand,
                           COALESCE(SUM(CASE WHEN method='combine' THEN kg END),0) comb
                    FROM harvests h WHERE season_year=? AND work_date BETWEEN ? AND ? AND {LIVE}{bf}''',
                (year,) + per + bp, one=True)
    closed = scalar(f'''SELECT COALESCE(SUM(wb.net_kg),0) FROM waybills wb JOIN trailer_loads tl ON tl.id=wb.load_id
                        WHERE wb.season_year=? AND wb.status<>'BEKOR' AND wb.document_date BETWEEN ? AND ?{lf}''',
                    (year,) + per + bp)
    arrived = q(f'''SELECT COUNT(*) n, COALESCE(SUM(wb.net_kg),0) kg FROM waybills wb JOIN trailer_loads tl ON tl.id=wb.load_id
                    WHERE wb.season_year=? AND wb.status<>'BEKOR' AND substr(wb.arrived_at,1,10) BETWEEN ? AND ?{lf}''',
                (year,) + per + bp, one=True)
    rec = q(f'''SELECT COUNT(*) n, COALESCE(SUM(nr.accepted_kg),0) acc, COALESCE(SUM(wb.net_kg),0) shipped,
                       COALESCE(SUM(nr.diff_kg),0) diff
                FROM nayman_receipts nr JOIN waybills wb ON wb.id=nr.waybill_id JOIN trailer_loads tl ON tl.id=wb.load_id
                WHERE wb.season_year=? AND wb.status<>'BEKOR' AND nr.received_date BETWEEN ? AND ?{lf}''',
            (year,) + per + bp, one=True)
    flow = {'harvest': harvest['total'], 'hand': harvest['hand'], 'combine': harvest['comb'], 'closed': closed,
            'sent': closed, 'arrived': arrived['kg'], 'accepted': rec['acc']}

    combines = []
    for c in q(f'''SELECT e.id, e.code, e.operator_name, e.ownership, e.photo_id, SUM(h.kg) kg,
                          GROUP_CONCAT(DISTINCT f.name) fields,
                          (SELECT p.thumb_path FROM photos p JOIN harvests x ON x.load_id=p.load_id WHERE x.combine_id=e.id
                            AND x.work_date BETWEEN ? AND ? AND p.voided_at IS NULL ORDER BY p.id DESC LIMIT 1) trip_thumb
                   FROM harvests h JOIN equipment e ON e.id=h.combine_id LEFT JOIN fields f ON f.id=h.field_id
                   WHERE h.season_year=? AND h.method='combine' AND h.work_date BETWEEN ? AND ? AND {LIVE}{bf}
                   GROUP BY e.id ORDER BY kg DESC''', per + (year,) + per + bp):
        from .accounting import combine_owner
        combines.append(dict(c, owner=combine_owner(c), thumb=_photo(c['photo_id']) or c['trip_thumb']))

    hand = [dict(r) for r in q(f'''SELECT f.id, f.code, f.name field_name, b.name brigadier_name, SUM(h.kg) kg,
                                          COUNT(DISTINCT h.worker_id) people,
                                          (SELECT p.thumb_path FROM photos p JOIN trailer_loads t2 ON t2.id=p.load_id
                                            WHERE t2.field_id=f.id AND p.voided_at IS NULL AND substr(p.uploaded_at,1,10) BETWEEN ? AND ?
                                            ORDER BY p.id DESC LIMIT 1) thumb
                                   FROM harvests h JOIN fields f ON f.id=h.field_id LEFT JOIN brigadiers b ON b.id=h.brigadier_id
                                   WHERE h.season_year=? AND h.method='hand' AND h.work_date BETWEEN ? AND ? AND {LIVE}{bf}
                                   GROUP BY f.id ORDER BY kg DESC''', per + (year,) + per + bp)]

    from .pq17 import period
    pq = None if brig else period(dan, gacha)
    punkt = {'sent': closed, 'arrived': arrived['kg'], 'accepted': rec['acc'], 'shipped': rec['shipped'],
             'diff': rec['diff'], 'n': rec['n'],
             'pct': round(rec['acc'] / rec['shipped'] * 100, 1) if rec['shipped'] else None,
             'moist': round(pq['moist'], 1) if pq and pq['moist'] is not None else None,
             'dirt': round(pq['dirt'], 1) if pq and pq['dirt'] is not None else None,
             'deduction': pq['deduction'] if pq else None, 'real': pq['kond'] if pq else None, 'pq_n': pq['n'] if pq else 0}

    ff = ' AND f.brigadier_id=?' if brig else ''
    area = scalar(f'SELECT COALESCE(SUM(area_ha),0) FROM fields f WHERE f.active=1{ff}', bp)
    started = scalar(f'''SELECT COALESCE(SUM(f.area_ha),0) FROM fields f WHERE f.active=1{ff} AND EXISTS
                         (SELECT 1 FROM harvests h WHERE h.field_id=f.id AND h.season_year=? AND h.voided_at IS NULL)''',
                     bp + (year,))
    fields = {'area': area, 'started': started, 'left': max(area - started, 0),
              'pct': round(started / area * 100) if area else 0}

    transport = {'on_way': scalar(f'''SELECT COUNT(*) FROM waybills wb JOIN trailer_loads tl ON tl.id=wb.load_id
                                      WHERE wb.season_year=? AND wb.status='YARATILDI' AND wb.arrived_at IS NULL{lf}''', (year,) + bp),
                 'queue': scalar(f'''SELECT COUNT(*) FROM waybills wb JOIN trailer_loads tl ON tl.id=wb.load_id
                                     WHERE wb.season_year=? AND wb.status='YARATILDI' AND wb.arrived_at IS NOT NULL{lf}''', (year,) + bp),
                 'in_field': scalar("SELECT COUNT(*) FROM trailer_loads WHERE status='OCHIQ'" + (' AND brigadier_id=?' if brig else ''), bp)}
    fuel = scalar('''SELECT COALESCE(SUM(liters),0) FROM fuel_ops WHERE kind='BERISH' AND voided_at IS NULL
                     AND substr(created_at,1,10) BETWEEN ? AND ?''', per)
    cash = scalar('''SELECT COALESCE(SUM(amount),0) FROM cash_entries WHERE direction='OUT' AND voided_at IS NULL
                     AND entry_date BETWEEN ? AND ?''', per) if money else None

    brigades = []
    for b in q('''SELECT b.id, b.name, b.full_name, b.photo_id,
                         COALESCE((SELECT SUM(area_ha) FROM fields f WHERE f.brigadier_id=b.id AND f.active=1),0) area,
                         COALESCE((SELECT SUM(kg) FROM harvests h WHERE h.brigadier_id=b.id AND h.season_year=?
                                    AND h.work_date BETWEEN ? AND ? AND h.voided_at IS NULL),0) kg,
                         COALESCE((SELECT SUM(kg) FROM harvests h WHERE h.brigadier_id=b.id AND h.season_year=?
                                    AND h.voided_at IS NULL),0) season_kg
                  FROM brigadiers b WHERE b.active=1''' + (' AND b.id=?' if brig else '') + ' ORDER BY kg DESC, b.name',
               (year,) + per + (year,) + bp):
        d = dict(b, thumb=_photo(b['photo_id']))
        d['per_ha'] = round(d['season_kg'] / d['area']) if d['area'] else None
        brigades.append(d)
    top = max([b['kg'] for b in brigades] + [1])
    for b in brigades:
        b['bar'] = round(b['kg'] / top * 100)
    return {'flow': flow, 'combines': combines, 'hand': hand, 'punkt': punkt, 'fields': fields, 'transport': transport,
            'fuel': fuel, 'cash': cash, 'brigades': brigades, 'brig_total': sum(b['kg'] for b in brigades),
            'comb_total': sum(c['kg'] for c in combines), 'hand_total': sum(h['kg'] for h in hand)}
