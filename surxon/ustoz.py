"""YORDAMCHI-USTOZ: the admin gives a staff member a task (a ready one or his own words); every time that person opens
the system the assistant greets them like a colleague, asks how they are, and teaches the task one step at a time — in
an Uzbek voice. Nothing is changed by the assistant: the person presses the buttons (so their name is written).
The person (or the admin) marks it done; it stays in the history."""
from .db import q, tx
from .security import audit
from .utils import UserError, clean_text, now_str

READY = {
    'nakladnoy': ('Eski nakladnoylarni tizimga kiritish', """\
1. Boshqaruv → Punkt blanklari: UY va PB blankalarni chop etish (faqat “Nechta” yoziladi, raqam o‘zi davom etadi).
2. Qog‘oz dala nakladnoylarini yuklarga ajratish: har yuk xati (hosil-qabuli.uz jadvali) uchun qaysi telashkalar borgan — sana, vaqt, kg bo‘yicha.
3. Har telashka uchun yangi PB blankani ruchkada to‘ldirish: TL raqami, qo‘l yoki kombayn, brigadirga ✓, dala kodi, kg, JAMI KG. Kg o‘zgartirilmaydi.
4. Punkt sahifasi: yangi UY varag‘ining QR kodini skanerlash → UY ochiladi.
5. “📷 DALA NAKLADNOYINI QO‘SHISH” → PB ni rasmga olish → sariq qatorlarni tekshirish → “✔ Qo‘shish”. Shu yukdagi hamma telashkalar uchun.
6. “Yopish”: yuk xati raqami, brutto, tara — hosil-qabuli.uz jadvalidan aynan → UY varag‘ini rasmga olish → “Qabul”.
7. Tizim “yuk xati allaqachon yozilgan” desa — u yuk tizimda bor, tegilmaydi, keyingisiga o‘tiladi.
8. Hammasi tugagach — jadvalni rasmga olib adminga yuborish."""),
    'punkt': ('Punktdagi kundalik ish', """\
1. Telashka kelganda: Punkt sahifasi → ochiq UY (yoki yangi UY varag‘ining QR kodini skanerlash).
2. “📷 DALA NAKLADNOYINI QO‘SHISH” → dala nakladnoyini (PB) yorug‘da, to‘liq rasmga olish.
3. Tizim o‘qigan qatorlarni tekshirish: sariq qator — aniq o‘qilmagan yoki dala boshqa brigadirniki → to‘g‘rilash → “✔ Qo‘shish”.
4. UY ga hamma telashkalar qo‘shilgach — “Yopish”: klaster tarozisi chekidagi yuk xati raqami, brutto, tara.
5. Muhrlangan UY varag‘ini rasmga olish → “Qabul”. Farq 1% dan katta bo‘lsa — sababni tanlash va 1 gap izoh.
6. Kun oxirida: ochiq qolgan UY yo‘qligini tekshirish; savol bo‘lsa — yordamchidan so‘rash."""),
    'buxgalter': ('Buxgalterning kundalik ishi', """\
1. Buxgalteriya → TEZ-PUL panel: bugun yozilgan talonlar, berilgan pul, to‘lanmaganlar — qizil narsa bormi.
2. Xarajat → tekshirilmaganlar: kassir yozgan xarajatlarni chekka qarab tasdiqlash (yordamchi tayyor karta beradi).
3. hosil-qabuli.uz → Paxta → Yuklab olish → Buxgalteriya → hosil-qabuli.uz jadvali → Excelni yuklash: “bizda yo‘q” va “kg farq” larni ko‘rish.
4. PQ-17 kelganlari: hosil-qabuli.uz da imzolash → klasterga faktura → Faktura (PQ-17) sahifasida belgilash.
5. Rahbar → Kunlik sverka: sariq yoki qizil brigadalar bo‘lsa — sababini topish (talon, punkt, dala nakladnoyi).
6. Kun oxirida: kassadagi real pulni sanab, “Kunni yopish” (farq bo‘lsa — sabab)."""),
}


def active(user_id):
    """This person's open task (the latest one), or None."""
    if not user_id:
        return None
    try:
        return q('SELECT * FROM yordamchi_guides WHERE user_id=? AND done_at IS NULL ORDER BY id DESC LIMIT 1', (user_id,), one=True)
    except Exception:            # before the migration ran
        return None


def listing():
    return q('''SELECT gd.*, u.full_name, u.username, u.role, c.full_name by_name FROM yordamchi_guides gd
                JOIN users u ON u.id=gd.user_id LEFT JOIN users c ON c.id=gd.created_by
                ORDER BY gd.done_at IS NOT NULL, gd.id DESC LIMIT 60''')


def assign(actor, user_id, title, body):
    if actor is None or actor.role != 'admin':
        raise UserError('Topshiriqni faqat Admin beradi.')
    u = q('SELECT * FROM users WHERE id=? AND active=1', (user_id,), one=True)
    if not u:
        raise UserError('Xodimni tanlang.')
    title, body = clean_text(title, 120), (body or '').strip()[:4000]
    if len(title) < 3 or len(body) < 10:
        raise UserError('Topshiriq nomi va qadamlarini yozing.')
    with tx() as db:
        db.execute('UPDATE yordamchi_guides SET done_at=?, done_by=? WHERE user_id=? AND done_at IS NULL',
                   (now_str(), actor.user_id, user_id))        # one open task at a time: the new one replaces it
        gid = db.execute('INSERT INTO yordamchi_guides(user_id, title, body, created_by, created_at) VALUES (?,?,?,?,?)',
                         (user_id, title, body, actor.user_id, now_str())).lastrowid
        db.execute("INSERT INTO yordamchi_messages(user_id, role, text, created_at) VALUES (?, 'reset', '', ?)",
                   (user_id, now_str()))       # a fresh conversation for the new task (the old one stays in the history)
        audit(db, actor, 'CREATE', 'yordamchi_guide', gid, new={'user_id': user_id, 'title': title})
    return gid


def finish(actor, gid):
    g = q('SELECT * FROM yordamchi_guides WHERE id=?', (gid,), one=True)
    if not g or g['done_at']:
        raise UserError('Topshiriq topilmadi yoki tugagan.')
    if actor is None or (actor.role != 'admin' and actor.user_id != g['user_id']):
        raise UserError('Bu topshiriq sizniki emas.')
    with tx() as db:
        db.execute('UPDATE yordamchi_guides SET done_at=?, done_by=? WHERE id=?', (now_str(), actor.user_id, gid))
        audit(db, actor, 'DONE', 'yordamchi_guide', gid, new={'title': g['title']})


def prompt(user):
    """The lines the assistant gets for a person with an open task."""
    g = active(user['id'])
    if not g:
        return ''
    return f"""

USTOZ REJIMI. Admin bu odamga topshiriq bergan: «{g['title']}» ({g['created_at'][:10]}). Qadamlari:
{g['body']}

Qanday o‘rgatasan:
- Suhbat boshlanganda (yoki “boshlaymiz”, “🚀 …” desa): ismi bilan iliq salomlash, hol-ahvol so‘ra (bitta qisqa gap,
  odamdek: “Charchamadingizmi?”), keyin topshiriqni bir gap bilan ayt va 1-qadamdan boshla.
- Har safar faqat BITTA qadam: nima qilish, qaysi tugmani bosish (sahifa_tugmasi bilan tugma ber), keyin “Bo‘ldimi?” deb so‘ra.
- “Bo‘ldi”, “ha”, “qildim” desa — qisqa maqta va keyingi qadamga o‘t. Tushunmasa — sabr bilan, boshqacha, soddaroq tushuntir.
- Qaysi qadamda ekanini esla (suhbat tarixidan). Odam boshqa savol bersa — javob ber va topshiriqqa qaytar.
- Hammasi tugasa: “Topshiriq tugadi, rahmat!” de va sahifa tepasidagi “✔ Tugatdim” tugmasini bosishni ayt.
"""
