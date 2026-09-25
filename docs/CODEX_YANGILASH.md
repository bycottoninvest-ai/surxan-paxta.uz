# Serverni yangilash (Codex uchun) — v2.6.0

Serverni qayta qurmang, `.env`, `data/` va Caddy sozlamasiga tegmang. Vaqtinchalik sslip override'ni qayta yoqmang.

```bash
cd /opt/surxan-paxta.uz
docker compose exec -T app flask --app app backup          # yangilashdan oldin izchil zaxira
git pull --ff-only                                          # kutilgan: 2.6.0 commiti yoki keyingisi
docker compose up -d --build
for i in $(seq 1 40); do curl -fsS https://surxan-paxta.uz/health && break; sleep 3; done   # "version":"2.6.0"
docker compose exec -T app flask --app app set-webhook      # channel_post yangilanishini qo'shadi (kanallarni tanish uchun)
docker compose exec -T app flask --app app smoke-check
```

Baza sxemasi o'zgarmaydi (faqat `tg_chats.bot_status` ustuni qo'shiladi, avtomatik).

## Yangilangandan keyin foydalanuvchi o'zi qiladi (saytda)
1. **Admin → Xodimlar va loginlar**: Rahbar — Salayev Aziz; Hisobchi — Asadbek, Mirjalol, Sadokat, Gulbohar; Punkt — Yunus;
   Buxgalter — Ibdulayev Asadbek. Parollar faqat ekranda bir marta chiqadi.
2. Ikkala kanalga (arxiv, hisobot) bittadan xabar yozadi → **Admin → Integratsiyalar → Telegram kanallari** → “Arxiv qilish” /
   “Hisobot qilish” → pastdagi jadvalda **Sinov**.
3. **Dalalar** (nomi, gektar, brigada) — ro'yxat foydalanuvchidan.

getUpdates bilan kanal qidirish ishlatilmaydi (shaxsiy xabarlarni yig'maslik uchun).
