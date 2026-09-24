"""Shared helpers for views."""
from flask import flash, jsonify, redirect, request

from ..db import get_db
from ..security import brigadier_scope, current_actor, validate_csrf, wants_json
from ..services import current_season
from ..utils import valid_uuid


def done(message, url, **data):
    """Finish a POST: JSON for fetch/offline-queue submissions, flash+redirect otherwise."""
    if wants_json():
        return jsonify(ok=True, message=message, redirect=url, **data)
    flash(message, 'success')
    return redirect(url)


def post_actor():
    validate_csrf()
    return current_actor()


def season_arg():
    raw = request.args.get('season', '')
    return int(raw) if raw.isdigit() else current_season(get_db())


def form_uuid():
    return valid_uuid(request.form.get('client_uuid'))


def checkbox(name):
    return request.form.get(name) in ('1', 'on', 'true', 'yes')


def scope():
    return brigadier_scope()


PER_PAGE = 50


def page_arg():
    raw = request.args.get('page', '1')
    return max(1, int(raw)) if raw.isdigit() else 1


def paginate(rows, page, per_page=PER_PAGE):
    """rows were fetched with LIMIT per_page+1; returns (rows, has_more)."""
    return rows[:per_page], len(rows) > per_page
