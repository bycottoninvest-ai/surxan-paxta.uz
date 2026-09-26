# Serverni yangilash (Codex uchun) — v2.18.0

Serverni qayta qurmang, `.env`, `data/` va Caddy sozlamasiga tegmang. Vaqtinchalik sslip override'ni qayta yoqmang.

```bash
cd /opt/surxan-paxta.uz
docker compose exec -T app flask --app app backup          # yangilashdan oldin izchil zaxira
git pull --ff-only                                          # kutilgan: v2.18.0 commiti
docker compose up -d --build
for i in $(seq 1 40); do curl -fsS https://surxan-paxta.uz/health && break; sleep 3; done   # "version":"2.18.0"
docker compose exec -T app flask --app app set-webhook      # channel_post + edited_message (Telegram jonli joylashuv) yangilanishlarini yoqadi — v2.17.0 da SHART
docker compose exec -T app flask --app app smoke-check
```

Baza avtomatik yangilanadi (sxema v9): `field_imports, field_assignments` va `fields.source_id/map_area_ha/area_source`, `field_seasons.crop` (v2.11.0 — dalalar importi); yangi jadvallar `fuel_stations, fuel_tickets, fuel_ticket_funds, fuel_prices, fuel_scans, fuel_ops` va `equipment.fuel_type/fuel_carrier/qr_token` (v2.10.0 — solyarka); `cash_corrections` (v2.9.0 — kassa tuzatishlari); yangi ustunlar `nayman_receipts.station_gross_kg/station_tare_kg` (v2.8.0 — punkt brutto/tara),
`trailer_loads.method/rate/rate_unit`, `workers.note`, `tg_chats.bot_status` (v2.7.0) qo'shiladi.
Eski yozuvlar o'zgarmaydi. Ilova ishga tushganda migratsiyadan oldin `data/` ichida `*.oldin-v*` zaxira nusxa oladi.

## v2.18.0 da nima o'zgardi
Sxema v11 → v13 (v12: GPS; v13: `media_requests.context/event/batch`, `media_items.field_id/equipment_id`).
- Jonli kuzatuv: direktor "+ Rasm/video so'rash" (`/kuzatuv/sorash`) — guruh (agronom, brigadir, haydovchi, kombaynchi,
  hisobchi, tarozi, punkt, yoqilg'i), dala (hozir o'sha dalada turganlar ham), brigada yoki xodim; bot har biriga yozadi.
  Muddat o'tsa bot bir marta eslatadi; baribir kelmasa bitta umumiy ogohlantirish. GPS texnika uzoq tursa haydovchisidan video.
  Lenta: dashboard, direktor paneli (`/rahbar/kuzatuv`), TV "Bugungi real voqealar" (faqat eskiz, video TV'ga yuklanmaydi).
- Nakladnoy (punkt nusxasi) 2 nusxada: "PUNKT TOMONIDAN TO'LDIRILADI" (brutto, tara, qabul kg, sana, F.I.Sh., imzo) va
  punkt muhri / jo'natuvchi muhri joylari. Punkt qabul qilgach: "Tasdiqlangan nakladnoy" PDF (punkt kg, elektron muhr,
  QR → ochiq `/tekshir/<id>/<kod>`); pechatli qog'oz rasmi punktdan yoki ofisdan (nakladnoy sahifasi) yuklanadi.
Avvalgi v12 qismi: (ishga tushganda `*.oldin-v11-*.bak`): yangi jadvallar `trackers, vehicle_positions, vehicle_days,
vehicle_works`; ustunlar `equipment.norm_field_lph/norm_road_lpkm/norm_idle_lph/work_hours`, `fuel_ops.purpose`.
- GPS treker (GT06): yangi konteyner `gps` (`python -m surxon.gt06`), TCP port **5023** ochiq bo'lishi kerak
  (Hetzner Firewall bo'lsa: Inbound TCP 5023 qo'shing). `docker compose up -d --build` uni o'zi ishga tushiradi.
- Admin → Texnikalar: texnikaga treker IMEI, salarka me'yori, ish vaqti; ulanmagan yangi trekerlar ro'yxati.
- Solyarka berishda "Nima ish uchun?" (Sozlamalar → fleet_work_types: nomi=l/soat).
- Direktor paneli → "Texnika xaritada": jonli holat (dalada / yo'lda / motor yoniq turibdi / turibdi / aloqa yo'q),
  bir joyda uzoq turish belgisi, bugungi yo'l, salarka me'yor va berilgan, dalalarda bajarilgan ishlar (qamrov %).
- Direktor paneli → "Dalalar tarixi": dalaga bosilsa mavsumdagi 1-/2-/3-terim va texnika ishlari (xaritada).
- Solyarka berish sahifasi (buxgalter/direktor uchun): "Bu solyarka qayerda ishlatildi" — keyingi quyishgacha GPS.
- Bekor qilinganlar: ishga tushganda bekor reysning hisobdan chiqmay qolgan tortishlari ham chiqariladi (ish haqi,
  kg, kombayn qayta hisoblanadi); ro'yxatlarda bekorlar faqat Admin "Arxiv: bekor" tugmasida.
- Dashboard: Bugun / Kecha / Mavsum. Buxgalteriya → Paxta → "Reys bo'yicha (kg va pul)": har reys qo'l terimi kg,
  odam, terimchilar puli, kombayn kg va puli, jami. Kombaynlar sahifasida egalik (o'zimizniki/tashqi) va salarka.

## v2.17.0 da nima o'zgardi
Sxema v10 → v11 (ishga tushganda `*.oldin-v10-*.bak`): trailer_loads.harvest_round/picked_cells/picked_ha/picked_split,
stations.lat/lon, users.avatar_path, tg_members.avatar_path, yangi jadval `staff_positions`.
- 1-, 2-, 3-terim: telashka ochilganda terim tanlanadi (dala bo'yicha avtomatik taklif).
- Terilgan joy: reys yopilganda dala xaritasidagi kataklar belgilanadi (yoki GPS nuqtalaridan taklif); qo'shni
  dalaga o'tsa, kg va haq maydon ulushiga qarab bo'linadi. Hisobot "Dala hosili (terimlar)" (`/dala-hosil`).
- Internetsiz ishlash: tortishlar telefonda navbatda saqlanadi, internet kelganda o'zi yuboriladi.
- Punkt joylashuvi (Admin → Punktlar, koordinata): punktga yetib kelish vaqti va dashboardda yo'ldagi traktor.
- Xodimlar xaritada (Direktor paneli): Telegram botga "jonli joylashuv" ulashgan va ilovada ishlayotgan xodimlar
  profil rasmi bilan; bosilsa bugungi yurgan yo'li. Buning uchun `set-webhook` qayta ishga tushirilishi SHART.

## v2.16.0 da nima o'zgardi
Sxema v9 → v10 (ishga tushganda `*.oldin-v9-*.bak`): harvests.lat/lon/gps_acc, trailer_loads.open_lat/open_lon/open_acc.
GPS: hisobchi telashka ochganda telefon joylashuvidan dala avtomatik tanlanadi (kontur ichida yoki 500 m gacha
eng yaqini); hisobchi bosh sahifasida jonli xarita (qaysi dalada turgani, yurganda nuqta ham yuradi); har tortish joyi saqlanadi; bosh sahifa xaritasida "Terilgan joylar" + tagida bugungi reyslarning haqiqiy rasmlari (bosilsa, o'sha reys nuqtalari ajraladi). Xaritada nomlar faqat yaqinlashtirganda.
Nakladnoy bekor qilinsa, dala reysi ham bekor bo'ladi; oldin shunday bekor qilinganlar ishga tushganda avtomatik hisobdan
chiqariladi (punkt qabul qilganlarga tegilmaydi). Bekor reys/nakladnoylar ro'yxatlarda faqat Adminga ko'rinadi.

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
