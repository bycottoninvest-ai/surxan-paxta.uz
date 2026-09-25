"""Kuzatuv: ask people for photos/videos via Telegram and see them here (manager/admin)."""
import json

from flask import Blueprint, current_app, render_template, request, url_for

from .. import kuzatuv as K
from ..db import q
from ..security import perm_required, require
from ..utils import UserError, parse_date, parse_int, today_str
from . import done, post_actor

bp = Blueprint('kuzatuv', __name__)


def _ids(name='member_ids'):
    return [v for v in request.form.getlist(name) if v.isdigit()]


@bp.route('/kuzatuv', methods=['GET', 'POST'])
@perm_required('kuzatuv.view')
def home():
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'remind':
            require('kuzatuv.request')
            K.remind(post_actor(), parse_int(request.form.get('request_id'), 'So‘rov'))
            return done('Eslatma yuborildi.', url_for('kuzatuv.home'))
        if action == 'cancel':
            require('kuzatuv.request')
            K.cancel(post_actor(), parse_int(request.form.get('request_id'), 'So‘rov'))
            return done('So‘rov bekor qilindi.', url_for('kuzatuv.home'))
        require('kuzatuv.request')
        ids = K.create_requests(post_actor(), _ids(), request.form.get('text', ''), kind=request.form.get('kind', 'any'),
                                deadline_min=request.form.get('deadline_min'))
        sent = sum(1 for rid in ids if K.deliver(rid))
        msg = f'{len(ids)} ta so‘rov yaratildi, {sent} tasi Telegramga yuborildi.'
        if sent < len(ids):
            msg += ' Qolganlari odam botni ochganda yoki guruhda ko‘ringanda avtomatik yuboriladi (ro‘yxatda sababi bor).'
        return done(msg, url_for('kuzatuv.home'))
    day = request.args.get('date') or today_str()
    try:
        day = parse_date(day)
    except UserError:
        day = today_str()
    member = request.args.get('member', '')
    member_id = int(member) if member.isdigit() else None
    page = int(request.args['page']) if request.args.get('page', '').isdigit() else 1
    rows = K.items(None if member_id else day, member_id, limit=49, offset=(page - 1) * 48)
    cfg = current_app.config['SURXON']
    return render_template('kuzatuv.html', day=day, stats=K.day_stats(day), items=rows[:48], has_more=len(rows) > 48,
                           page=page, member_id=member_id, open_reqs=K.open_requests(), requests=K.recent_requests(day),
                           members=K.members('FAOL'), templates=K.TEMPLATES, kinds=K.KINDS, statuses=K.STATUSES,
                           bot_ok=bool(cfg.TELEGRAM_BOT_TOKEN), groups=q('SELECT * FROM tg_chats WHERE is_work=1'))


@bp.post('/kuzatuv/fayl/<int:item_id>/yashirish')
@perm_required('kuzatuv.manage')
def void(item_id):
    K.void_item(post_actor(), item_id, request.form.get('reason', ''))
    return done('Yashirildi (o‘chirilmadi, audit saqlandi).', request.referrer or url_for('kuzatuv.home'))


@bp.route('/kuzatuv/odamlar', methods=['GET', 'POST'])
@perm_required('kuzatuv.view')
def people():
    if request.method == 'POST':
        require('kuzatuv.manage')
        action = request.form.get('action')
        if action == 'group':
            n = K.set_work_group(post_actor(), request.form.get('chat_id'), request.form.get('on') == '1')
            return done('Guruh holati saqlandi.' + (f' {n} ta odam faollashdi.' if n else ''), url_for('kuzatuv.people'))
        if action == 'link':
            K.new_link_code(post_actor(), parse_int(request.form.get('member_id'), 'Odam'))
            return done('Yangi taklif havolasi tayyor.', url_for('kuzatuv.people'))
        mid = parse_int(request.form.get('member_id'), 'Odam', required=False)
        mid = K.save_member(post_actor(), mid, full_name=request.form.get('full_name', ''),
                            role_label=request.form.get('role_label', ''), status=(request.form.getlist('status') or [None])[-1] or None,
                            equipment_id=parse_int(request.form.get('equipment_id'), 'Texnika', required=False),
                            field_id=parse_int(request.form.get('field_id'), 'Dala', required=False),
                            user_id=parse_int(request.form.get('user_id'), 'Foydalanuvchi', required=False))
        return done('Saqlandi.', url_for('kuzatuv.people', _anchor=f'm{mid}'))
    rows = K.members()
    return render_template('kuzatuv_people.html', rows=rows, links={r['id']: K.invite_link(r) for r in rows},
                           bot_user=current_app.config['SURXON'].TELEGRAM_BOT_USERNAME,
                           chats=q('SELECT * FROM tg_chats ORDER BY is_work DESC, last_seen_at DESC'),
                           equipment=q("SELECT id, code, kind FROM equipment WHERE active=1 ORDER BY kind, code"),
                           fields=q('SELECT id, code, name FROM fields WHERE active=1 ORDER BY code'),
                           users=q('SELECT id, full_name, username FROM users WHERE active=1 ORDER BY full_name'))


@bp.route('/kuzatuv/jadval', methods=['GET', 'POST'])
@perm_required('kuzatuv.view')
def rules():
    if request.method == 'POST':
        require('kuzatuv.manage')
        rid = parse_int(request.form.get('rule_id'), 'Jadval', required=False)
        if request.form.get('action') == 'toggle':
            r = q('SELECT * FROM media_rules WHERE id=?', (rid,), one=True)
            if not r:
                raise UserError('Jadval topilmadi.')
            K.save_rule(post_actor(), rid, title=r['title'], text=r['text'], kind=r['kind'], times=r['times'],
                        weekdays=r['weekdays'], member_ids=json.loads(r['members_json']), deadline_min=r['deadline_min'],
                        active=not r['active'])
            return done('Jadval ' + ('to‘xtatildi.' if r['active'] else 'yoqildi.'), url_for('kuzatuv.rules'))
        K.save_rule(post_actor(), rid, title=request.form.get('title', ''), text=request.form.get('text', ''),
                    kind=request.form.get('kind', 'any'), times=request.form.get('times', ''),
                    weekdays=''.join(request.form.getlist('weekdays')), member_ids=_ids(),
                    deadline_min=request.form.get('deadline_min'), active=True)
        return done('Jadval saqlandi. Belgilangan vaqtda so‘rovlar o‘zi yuboriladi.', url_for('kuzatuv.rules'))
    rows = q('SELECT * FROM media_rules ORDER BY active DESC, id DESC')
    names = {m['id']: m['full_name'] for m in K.members()}
    return render_template('kuzatuv_rules.html', rows=rows, names=names, members=K.members('FAOL'), kinds=K.KINDS,
                           templates=K.TEMPLATES, json=json,
                           edit=q('SELECT * FROM media_rules WHERE id=?', (int(request.args['edit']),), one=True)
                           if request.args.get('edit', '').isdigit() else None)
