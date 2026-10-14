"""YORDAMCHI (AI): the chat page and its two calls. Read-only for the data; see surxon.assistant."""
from flask import Blueprint, abort, g, jsonify, render_template, request

from .. import assistant as A
from ..security import login_required
from ..utils import UserError

bp = Blueprint('assistant', __name__)


def _gate():
    if not A.allowed(g.user):
        abort(404)


@bp.get('/ai')
@login_required
def page():
    _gate()
    name = (g.user['full_name'] or g.user['username'] or '').split(' ')[0]
    return render_template('assistant.html', messages=A.history(g.user['id'], limit=40), name=name,
                           chips=A.suggestions(g.user))


@bp.post('/ai/savol')
@login_required
def ask():
    if not A.allowed(g.user):
        return jsonify(ok=False, error='Yordamchi siz uchun yoqilmagan.'), 403
    try:
        return jsonify(ok=True, **A.ask(g.user, request.form.get('text')))
    except UserError as err:
        return jsonify(ok=False, error=str(err)), 422


@bp.post('/ai/yangi')
@login_required
def reset():
    _gate()
    A.reset(g.user['id'])
    return jsonify(ok=True)
