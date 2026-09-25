# Serverni yangilash (Codex uchun) — v2.8.0

Serverni qayta qurmang, `.env`, `data/` va Caddy sozlamasiga tegmang. Vaqtinchalik sslip override'ni qayta yoqmang.

```bash
cd /opt/surxan-paxta.uz
docker compose exec -T app flask --app app backup          # yangilashdan oldin izchil zaxira
git pull --ff-only                                          # kutilgan: v2.8.0 commiti
docker compose up -d --build
for i in $(seq 1 40); do curl -fsS https://surxan-paxta.uz/health && break; sleep 3; done   # "version":"2.8.0"
docker compose exec -T app flask --app app set-webhook      # channel_post yangilanishini qo'shadi (kanallarni tanish uchun)
docker compose exec -T app flask --app app smoke-check
```

Baza avtomatik yangilanadi: yangi ustunlar `nayman_receipts.station_gross_kg/station_tare_kg` (v2.8.0 — punkt brutto/tara),
`trailer_loads.method/rate/rate_unit`, `workers.note`, `tg_chats.bot_status` (v2.7.0) qo'shiladi.
Eski yozuvlar o'zgarmaydi. Ilova ishga tushganda migratsiyadan oldin `data/` ichida `*.oldin-v*` zaxira nusxa oladi.

## v2.8.0 da nima o'zgardi
Punkt operatori ekrani: kelgan telashkalar ro'yxati (rasm bilan) → brutto/tara (netto o'zi) → farq sababi (8 ta, “Boshqa sabab”da matn)
→ “Qabul qilindi” + qabul hujjati PDF (narxsiz). Hisob-kitob, rollar, rahbar paneli o'zgarmagan.

## Qaytarish (kerak bo'lsa)
```bash
cd /opt/surxan-paxta.uz
git log --oneline -3          # oldingi commitni ko'ring
git checkout <oldingi_commit> && docker compose up -d --build
```
Yangi ustunlar eski versiyaga xalaqit bermaydi (ular shunchaki ishlatilmaydi).

## Yangilangandan keyin foydalanuvchi o'zi qiladi (saytda)
1. **Admin → Xodimlar va loginlar**: Rahbar — Salayev Aziz; Hisobchi — Asadbek, Mirjalol, Sadokat, Gulbohar; Punkt — Yunus;
   Buxgalter — Ibdulayev Asadbek. Parollar faqat ekranda bir marta chiqadi.
2. Ikkala kanalga (arxiv, hisobot) bittadan xabar yozadi → **Admin → Integratsiyalar → Telegram kanallari** → “Arxiv qilish” /
   “Hisobot qilish” → pastdagi jadvalda **Sinov**.
3. **Dalalar** (nomi, gektar, brigada) — ro'yxat foydalanuvchidan.

getUpdates bilan kanal qidirish ishlatilmaydi (shaxsiy xabarlarni yig'maslik uchun).
