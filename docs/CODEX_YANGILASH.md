# Serverni yangilash (Codex uchun) — v2.15.0

Serverni qayta qurmang, `.env`, `data/` va Caddy sozlamasiga tegmang. Vaqtinchalik sslip override'ni qayta yoqmang.

```bash
cd /opt/surxan-paxta.uz
docker compose exec -T app flask --app app backup          # yangilashdan oldin izchil zaxira
git pull --ff-only                                          # kutilgan: v2.15.0 commiti
docker compose up -d --build
for i in $(seq 1 40); do curl -fsS https://surxan-paxta.uz/health && break; sleep 3; done   # "version":"2.15.0"
docker compose exec -T app flask --app app set-webhook      # channel_post yangilanishini qo'shadi (kanallarni tanish uchun)
docker compose exec -T app flask --app app smoke-check
```

Baza avtomatik yangilanadi (sxema v9): `field_imports, field_assignments` va `fields.source_id/map_area_ha/area_source`, `field_seasons.crop` (v2.11.0 — dalalar importi); yangi jadvallar `fuel_stations, fuel_tickets, fuel_ticket_funds, fuel_prices, fuel_scans, fuel_ops` va `equipment.fuel_type/fuel_carrier/qr_token` (v2.10.0 — solyarka); `cash_corrections` (v2.9.0 — kassa tuzatishlari); yangi ustunlar `nayman_receipts.station_gross_kg/station_tare_kg` (v2.8.0 — punkt brutto/tara),
`trailer_loads.method/rate/rate_unit`, `workers.note`, `tg_chats.bot_status` (v2.7.0) qo'shiladi.
Eski yozuvlar o'zgarmaydi. Ilova ishga tushganda migratsiyadan oldin `data/` ichida `*.oldin-v*` zaxira nusxa oladi.

## v2.15.0 da nima o'zgardi
Tuzatish: bekor qilingan reysning tarozi/dala og'irligi "Umumiy tarozi (netto)", dala hosildorligi, brigadalar va
grafiklarda hisoblanib qolardi — endi chiqmaydi. Yangi: Admin uchun "Reysni davom ettirish" (telashka sahifasida) —
to'lmasdan xato yopilgan reys qayta ochiladi, hisobchi shu reysga davom etadi; yopilganda nakladnoy o'sha raqam bilan
yangi og'irlikda qayta chiqadi. Punkt qabul qilgan reysga ishlamaydi. Baza o'zgarmaydi.

## v2.14.1 da nima o'zgardi
Zaxira sahifasi: vaqt sana ko'rinishida, Storage Box ulangan bo'lsa oxirgi muvaffaqiyatli tashqi nusxa vaqti (yoki xato);
Integratsiyalar'dagi TV izohi yangilandi. Baza o'zgarmaydi.

## v2.14.0 da nima o'zgardi
Hetzner Storage Box (serverdan tashqari zaxira) Admin → Integratsiyalar'dan ulanadi: host, foydalanuvchi, parol.
Parol `rclone obscure` bilan `data/rclone.conf` (600) ga yoziladi, ekranda/auditda ko'rinmaydi. Web va `backup`
konteyneri har safar shu faylni tekshiradi (qayta ishga tushirish shart emas). `.env` dagi OFFSITE_RCLONE_REMOTE bo'lsa,
u ustun. Baza o'zgarmaydi.

## v2.13.0 da nima o'zgardi
Ofis televizori uchun yangi `/tv` (1920×1080, istalgan ekranga moslashadi): bugun/mavsum terim, mavsum rejasi
(Sozlamalar → season_target_kg), punkt qabuli va farq, reyslar oqimi, telashkalar holati, kombaynlar, dalalar xaritasi
(kg/ga rangi), daladan so'nggi 3 rasm (faqat telashka/dala rasmlari), brigadalar reytingi, soatlik grafik (bugun/kecha),
solyarka, eng yaxshi 5 terimchi (tv_show_workers=0 bilan o'chadi), jonli lenta, ob-havo. TV'da pul hech qachon
ko'rsatilmaydi. Har 30 soniyada yangilanadi, internet uzilsa "Eski ma'lumot". Baza o'zgarmaydi.

## v2.12.2 da nima o'zgardi
Admin uchun “Reysni to'liq bekor qilish” (telashka sahifasida): test yoki xato reysni istalgan holatda — dala
tortishlari, nakladnoy va punkt qabuli bilan birga — bir bosishda bekor qiladi. Sabab majburiy; hech narsa
o'chirilmaydi (qabul yozuvi audit tarixiga to'liq ko'chiriladi). Ishchiga pul berilgan bo'lsa, ruxsat bermaydi.
Faqat Admin. Baza o'zgarmaydi.

## v2.12.1 da nima o'zgardi
Google Sheets'ni Admin → Integratsiyalar sahifasidan ulash (serverga kirmasdan): jadval havolasi + xizmat akkaunti
.json kaliti. Kalit `data/google-service-account.json` (600) da saqlanadi, ekranda faqat xizmat akkaunti emaili.
`.env` dagi GOOGLE_SHEETS_ID bo'lsa, u ustun. Baza o'zgarmaydi.

## v2.12.0 da nima o'zgardi
Direktor paneli (telefon, faqat ko'rish): /rahbar — 6 karta (Paxta, Yo'lda, Solyarka, Kassa, Ish haqi, Tekshirish),
Bugun/Hafta/Mavsum, so'nggi hodisalar, reys/ishchi/kassa/solyarka tafsilotlari, qidiruv (reys, texnika, ishchi, tiket).
Har 20 soniyada yangilanadi, internet yo'q bo'lsa "Eski ma'lumot". Direktor/admin telefonda kirganda shu panel ochiladi,
kompyuterda eski dashboard. Yozish yo'q. Baza o'zgarmaydi.

## v2.11.2 da nima o'zgardi
Google xarita kalitini Admin → Integratsiyalar sahifasida kiritish (serverga kirmasdan). Kalit Google Cloud’da sayt va
Map Tiles API bilan cheklangan brauzer kaliti; ekranda faqat oxirgi 4 belgisi ko‘rinadi. `.env` dagi kalit bo‘lsa, u ustun.
Baza o‘zgarmaydi.

## v2.11.1 da nima o'zgardi
Tuzatish: yangilanishdan keyin telefon/kompyuter eski dizayn (CSS) va belgilarni ko‘rsatib qolardi (service worker keshi).
Endi statik fayllar avval tarmoqdan olinadi; kesh faqat internet yo‘qligida ishlatiladi. Baza o‘zgarmaydi.

## v2.11.0 da nima o'zgardi
Dalalar: KML/GeoJSON import (tekshirish → nom/brigadir/maydon → tasdiqlash; qayta import dublikatsiz), xaritada yangi dala
chizish va chegarani tahrirlash, maydon manbai (xarita / tasdiqlangan), brigadir tarixi, dala sahifasida haqiqiy hisoblar.
Google sun’iy yo‘ldosh foni ixtiyoriy: kalit faqat serverda `bash tools/sozlash.sh google` bilan kiritiladi (chatga yuborilmaydi).
Sxema v8 → v9: ishga tushganda `*.oldin-v8-*.bak`. Dalalarni haqiqiy bazaga foydalanuvchining o‘zi saytda tasdiqlab saqlaydi.

## v2.10.0 da nima o'zgardi
Solyarka moduli: yangi rol “Yoqilg‘i mas’uli” (/yoqilgi — faqat QR bilan olish/berish, berishda kamera rasmi),
buxgalter uchun /yoqilgi/boshqaruv (zapravka, tiket, narx, pul qo'shish, sverka bilan yopish, bekor qilish, QR chop etish).
Tiket puli bitta “Yoqilg‘i” xarajati; olish/berish qayta xarajat yaratmaydi. Sxema v7 → v8: ishga tushganda `*.oldin-v7-*.bak`.
Haqiqiy bazaga namunaviy tiket/litr kiritilmasin.

## v2.9.0 da nima o'zgardi
Buxgalter va kassir telefonda “Mening kassam” (/hamyon): ishchiga pul berish, xarajat, kassaga kirim, bugungi amallar,
tuzatish (asl yozuv saqlanadi, kassir so'rovini buxgalter tasdiqlaydi). Har amal: Tekshirish → Tasdiqlash.
Buxgalter va kassir kirganda /hamyon ochiladi. Eski buxgalteriya sahifalari, hisobotlar, rahbar paneli o'zgarmagan.
Sxema v6 → v7 bo'lgani uchun ilova ishga tushganda `data/` ichida `*.oldin-v6-*.bak` nusxa o'zi olinadi.

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
