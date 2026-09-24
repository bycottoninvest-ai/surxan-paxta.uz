# Zaxira nusxa va tiklash

## Nima saqlanadi

- `data/surxon.sqlite3` — butun hisob: mavsumlar, terim, reyslar, tarozi, nakladnoy, Nayman, to‘lov, kassa, audit.
- `data/uploads/` — barcha rasmlar (asl nusxa va kichik nusxa).

## Avtomatik zaxira

`docker-compose.yml` dagi `backup` xizmati har 24 soatda ishlaydi:

1. **Baza** SQLite *online backup API* orqali nusxalanadi. Ilova ishlab turganda ham nusxa to‘liq chiqadi: WAL faylidagi so‘nggi yozuvlar ham kiradi. Oddiy `cp` buni kafolatlamaydi. Nusxa `PRAGMA integrity_check` bilan tekshiriladi.
2. **Rasmlar:** har kuni oxirgi 2 kunlik yangi rasmlar olinadi, yakshanba kuni esa to‘liq arxiv qilinadi.
3. Fayllar `data/backups/` ga yoziladi. Kunlik nusxalar `BACKUP_KEEP_DAYS` (30) kun saqlanadi, to‘liq foto arxivlardan oxirgi 8 tasi qoladi.

Qo‘lda zaxira olish: **Admin → Zaxira nusxa → Hozir zaxira olish** yoki serverda:

```bash
docker compose exec app flask --app app backup
```

## Serverdan tashqariga nusxa (majburiy)

Server buzilsa, undagi nusxalar ham yo‘qoladi. Haftada kamida bir marta **Admin → Zaxira nusxa** sahifasidan oxirgi `surxon_db_*.sqlite3` va `surxon_photos_*_full.tar.gz` ni yuklab olib, boshqa joyda saqlang (kompyuter yoki bulut). Yopilgan har bir mavsum uchun alohida nusxani doimiy saqlang.

## Tiklash (tekshirilgan tartib)

```bash
cd /opt/surxan-paxta.uz
docker compose stop app backup
cp data/surxon.sqlite3 data/surxon.sqlite3.buzilgan.$(date +%F)        # joriy holatni saqlab qo‘ying
rm -f data/surxon.sqlite3-wal data/surxon.sqlite3-shm
cp data/backups/surxon_db_YYYYMMDD_HHMMSS.sqlite3 data/surxon.sqlite3
# rasmlar: avval oxirgi to‘liq arxiv, keyin undan keyingi kunliklar (eskidan yangiga)
tar -xzf data/backups/surxon_photos_YYYYMMDD_HHMMSS_full.tar.gz -C data/uploads
tar -xzf data/backups/surxon_photos_<keyingi_kun>.tar.gz -C data/uploads
docker compose start app backup
curl https://surxan-paxta.uz/health
```

Avtomatik test `tests/test_workflow.py::test_backup_from_live_wal_database_restores` quyidagilarni tekshiradi:

- ochiq yozuvchi ulanish bor paytda (yozuv hali WAL faylida turganida) zaxira olinadi va bu yozuv nusxaga tushadi;
- nusxa `integrity_check` dan o‘tadi;
- tiklangan fayl bilan ilova ishga tushadi.

## Yangilash (migratsiya)

Baza sxemasining versiyasi `schema_version` jadvalida saqlanadi. Yangi versiya chiqqanda:

```bash
git pull
docker compose exec app flask --app app backup      # avval zaxira
docker compose up -d --build                         # ilova ishga tushishda migratsiyani o‘zi bajaradi
```

Migratsiyalar faqat qo‘shadi: ustun va jadval qo‘shiladi, eski ma’lumot o‘chirilmaydi. Yopilgan mavsumlarning maydonlari `field_seasons` jadvalida qotirilgan, shuning uchun keyingi yilgi o‘zgarishlar eski hisobotlarni o‘zgartirmaydi.
