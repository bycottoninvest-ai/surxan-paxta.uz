"""YORDAMCHI (AI): the chat page and its two calls. Read-only for the data; see surxon.assistant."""
from flask import Blueprint, Response, abort, g, jsonify, render_template, request

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
    from .. import gemini
    return render_template('assistant.html', messages=A.history(g.user['id'], limit=40), name=name,
                           chips=A.suggestions(g.user), uz_voice=gemini.enabled(), start=request.args.get('boshla') == '1')


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


@bp.post('/ai/ovoz')
@login_required
def voice():
    """The answer read aloud in an Uzbek voice (Gemini). Only the person's own recent answers; else the phone reads it."""
    from .. import gemini
    from ..security import validate_csrf
    validate_csrf()
    if not A.allowed(g.user):
        abort(404)
    want = gemini.speakable(request.form.get('text'))
    if want not in {gemini.speakable(m['text']) for m in A.history(g.user['id'], limit=8) if m['role'] == 'assistant'}:
        return jsonify(ok=False, error='Bu matn yordamchining javobi emas.'), 422
    try:
        return Response(gemini.tts(want), mimetype='audio/wav', headers={'Cache-Control': 'private, max-age=86400'})
    except UserError as err:
        return jsonify(ok=False, error=str(err)), 422


@bp.post('/ai/bajar/<int:action_id>')
@login_required
def act(action_id):
    """The person confirms the change the assistant prepared — it runs in their name through the ordinary service."""
    from ..security import current_actor, validate_csrf
    validate_csrf()
    if not A.allowed(g.user):
        abort(404)
    try:
        return jsonify(ok=True, **A.act(g.user, current_actor(), action_id, request.form))
    except UserError as err:
        return jsonify(ok=False, error=str(err)), 422
