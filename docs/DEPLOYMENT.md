# Serverga o‘rnatish (surxan-paxta.uz)

## 1. Kerakli narsalar

- VPS: Ubuntu 22.04/24.04, kamida 2 GB RAM va 40 GB disk (rasmlar ko‘payadi).
- `surxan-paxta.uz` domeni boshqaruviga kirish.
- BotFather'dan olingan Telegram bot tokeni. U faqat serverdagi `.env` fayliga yoziladi.

## 2. DNS

| Turi | Nomi | Qiymati |
|---|---|---|
| A | `surxan-paxta.uz` | server IP |
| A yoki CNAME | `www` | server IP / `surxan-paxta.uz` |

## 3. O‘rnatish

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-v2 git
sudo git clone https://github.com/bycottoninvest-ai/surxan-paxta.uz /opt/surxan-paxta.uz
cd /opt/surxan-paxta.uz
sudo cp .env.example .env
sudo nano .env          # SECRET_KEY, TELEGRAM_* qiymatlarini kiriting
sudo mkdir -p data && sudo chown -R 1000:1000 data
sudo docker compose up -d --build
curl https://surxan-paxta.uz/health      # {"ok": true, ...}
```

Caddy HTTPS sertifikatini o‘zi oladi. Ilova ishga tushganda migratsiya o‘zi bajariladi, 3 ta brigada va boshlang‘ich texnika (3 traktor, 4 telashka, 2 kombayn) yaratiladi.

## 4. Birinchi kirish

- `ADMIN_PASSWORD` bo‘sh qoldirilgan bo‘lsa, parol `data/BIRINCHI_ADMIN_PAROLI.txt` faylida bo‘ladi. Kirgandan keyin tizim parolni almashtirishni talab qiladi, shundan so‘ng bu faylni o‘chiring.
- **Admin → Dalalar:** kontur raqamlari va gektarlarni kiriting. Xarita chegarasini dala sahifasida chizish mumkin.
- **Admin → Foydalanuvchilar:** har bir xodimga login bering. Brigadirni o‘z brigadasiga bog‘lang.
- **Sozlamalar:** narx, ish haqi stavkasi va to‘lov qoidasini **faqat tasdiqlangandan keyin** kiriting.

## 5. Telegram bot

```bash
docker compose exec app flask --app app set-webhook
```

Admin → Foydalanuvchilar → “Ulash kodi” ni bosing. Xodim botga `/start KOD` yuboradi va uning Telegram akkaunti tizimdagi loginiga bog‘lanadi.

## 6. TV ekran

Admin → Sozlamalar → Integratsiyalar → TV ulanishini yoqing → **TV kaliti yaratish**. Chiqqan havolani (`https://surxan-paxta.uz/tv?k=...`) televizor brauzerida bir marta oching. Ekran har 30 soniyada yangilanadi. Aloqa uzilsa, qizil ogohlantirish chiqadi.

## 7. Mahalliy sinov (kompyuterda)

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env    # SECRET_KEY=istalgan, COOKIE_SECURE=0, ADMIN_PASSWORD=...
python app.py           # http://127.0.0.1:5000
flask --app app demo-data --yes   # faqat bo‘sh sinov bazasiga namunaviy ma’lumot
pytest -q
```
