# Tekshiruv natijalari

Sana: 2026-09-25. Ishga tushirish: `pytest -q` (31 test) va `python tools/perf.py`.

## 1. Tekshirildi va o‘tdi

**Avtomatik testlar: 31 / 31 o‘tdi.**

| Test | Nima tekshirildi |
|---|---|
| `test_full_chain_field_to_payment` | Brigadir: reys ochish → 2 ishchi (vergulli kg bilan) + kombayn → rasm → TOLDI. TOLDIdan keyin terim qo‘shib bo‘lmaydi. Tarozi: brutto, keyin tara → netto → **PA-000001**. Nayman qabuli → to‘lov → qarz hisobi. Dala kg, tarozi netto va Nayman kg alohida saqlanadi. Auditda har qadam uchun kim bajargani bor. Dashboard, nakladnoy sahifasi va foto arxivda ko‘rinadi. |
| `test_same_trailer_two_trips_same_day_no_double_count` | Bir telashka, bir kun, ikki reys: 1-reys 100 kg, 2-reys 50 kg. Takroriy hisob yo‘q. Tortilmagan telashkaga yangi reys ochilmaydi. |
| `test_double_click_and_offline_replay_create_one_record` | Bir xil `client_uuid` ikki marta yuborilsa, bitta yozuv. 3 daqiqa ichida bir xil ishchi va kg uchun tasdiq so‘raladi. |
| `test_parallel_weighings_get_unique_sequential_numbers` | 4 ta parallel tarozi yakuni → PA-000001…000004, takror yo‘q, xato yo‘q. |
| `test_brigadier_cannot_touch_other_brigade` | Boshqa brigada dalasi, reysi, TOLDI, tarozi, kassa va admin sahifalari → rad etiladi. |
| `test_service_layer_enforces_roles_even_without_views` | Rol tekshiruvi xizmat qatlamida: Telegram ham, web ham chetlab o‘ta olmaydi. |
| `test_bad_photo_leaves_no_partial_record` | Buzuq rasm yuborilganda TOLDI saqlanmaydi, qisman yozuv va audit qoldig‘i qolmaydi. Rasmsiz TOLDI rad etiladi. |
| `test_weighing_correction_updates_document_and_keeps_history` | Tarozi xodimi qayta yoza olmaydi. Rahbar sabab bilan tuzatadi → nakladnoy nettosi ham yangilanadi, eski qiymat auditda qoladi. |
| `test_large_difference_requires_reason` | Ichki kg va netto farqi 2% dan katta bo‘lsa, sabab majburiy. |
| `test_input_validation` | NaN, inf, manfiy, 0, harf, juda katta kg; manfiy summa; kelajakdagi sana → rad etiladi. |
| `test_audit_log_is_append_only` | Audit jadvalini UPDATE/DELETE qilib bo‘lmaydi (bazadagi trigger). |
| `test_void_is_soft_and_excluded_from_totals` | Bekor qilish sabab bilan; yozuv o‘chirilmaydi, jamiga kirmaydi. |
| `test_closed_season_is_frozen_and_old_reports_do_not_change` | Mavsum yopildi. Keyin dala gektari o‘zgartirildi, eski mavsum kg/ga o‘zgarmadi. Yopilgan mavsumga yozish rad etiladi. |
| `test_photos_are_private` | Loginsiz rasm yo‘q, `/static/uploads` yo‘q, boshqa brigada rasmi 403, yo‘l bilan chetlab o‘tish (path traversal) yo‘q. |
| `test_service_worker_never_caches_private_pages` | Service worker faqat `/static/` fayllarini keshlaydi, eski v1 kesh o‘chiriladi. |
| `test_login_throttle_and_password_change` | 8 ta xato urinishdan keyin blok; parolni almashtirish ishlaydi. |
| `test_backup_from_live_wal_database_restores` | WALdagi yozuv zaxiraga tushadi, integrity_check ok, tiklangan fayl bilan ilova ishga tushadi. |
| `test_worker_payment_and_cash_rules` | Boshlang‘ich qoldiqsiz chiqim yo‘q; ikkinchi boshlang‘ich qoldiq yo‘q; avans; kassadan xarajat; stavka × kg. |
| `test_telegram` (4 ta) | Noto‘g‘ri webhook kaliti; ulanmagan akkaunt; kod orqali ulash; bot orqali reys → bir necha qatorli terim → TOLDI (rasmsiz rad etiladi) → brutto/tara → PA-000001. Telegram bir xil updateni qayta yuborsa, takror yozuv yo‘q. Rolga zid tugma bosilsa, rad etiladi. |
| `test_integrations` (5 ta) | ERP kaliti xeshlangan va bir marta ko‘rsatiladi; 401/403/405/503; kalitni almashtirish; filtr, sahifalash, `updated_since`; noma’lum narx yoki qoldiq `null` + `not_calculated` (0 emas); TV: kalitsiz 403, pul va ismlar yo‘q. |
| `test_pages` (4 ta) | 40+ sahifa × 6 rol, kompyuter va telefon, bo‘sh va ma’lumotli bazada — 500 xato yo‘q. Telefonda yengil bosh sahifa, og‘ir kutubxonalar yuklanmaydi. |

## 2. Telefon tezligi (o‘lchandi)

Sharoit: Chrome, 390×844 ekran, protsessor 4 marta sekinlashtirilgan (arzon Android), DevTools tarmoq cheklovi. Server: gunicorn + gzip, brigadir logini, namunaviy ma’lumot.

| Sahifa | Tarmoq | Kesh | Hajm | Sahifa ko‘rindi (DCL) | To‘liq yuklandi |
|---|---|---|---|---|---|
| Brigadir bosh sahifa | Slow 3G (400 kbit/s, 400 ms) | birinchi | 33 KB | 1,5 s | 1,5 s |
| Terim kiritish | Slow 3G | birinchi | 40 KB | 1,5 s | 1,5 s |
| Telashkalar (rasmlar bilan) | Slow 3G | birinchi | 51 KB | 1,5 s | 2,1 s |
| Brigadir bosh sahifa | Slow 3G | qayta | 3 KB | 0,5 s | 0,5 s |
| Terim kiritish | Slow 3G | qayta | 5 KB | 0,5 s | 0,5 s |
| Brigadir bosh sahifa | 2G (250 kbit/s, 800 ms) | birinchi | 33 KB | 2,7 s | 2,7 s |
| Terim kiritish | 2G | birinchi | 40 KB | 2,7 s | 2,7 s |
| Terim kiritish | 2G | qayta | 5 KB | 1,0 s | 1,0 s |

Optimallashtirishdan oldin brigadir bosh sahifasi 312 KB edi va Slow 3G'da 3,0–3,8 s da yuklanardi. Qilingan ishlar:

- logolar 44 KB dan 6–8 KB ga siqildi;
- telefonda bezak rasmlari o‘chirildi, web-shrift olib tashlandi;
- matnli fayllar gzip bilan siqiladi;
- telefon bosh sahifasida grafik va xarita kutubxonalari yuklanmaydi (ular faqat kompyuter dashboardida).

Internet uzilsa, terim, TOLDI va reys ochish formalari telefonning IndexedDB xotirasiga saqlanadi. Yuqorida “Internet yo‘q · navbatda N ta” deb ko‘rinadi. Internet kelganda yozuvlar o‘sha `client_uuid` bilan yuboriladi, server ularni bir marta saqlaydi. “Saqlandi” xabari faqat server javobidan keyin chiqadi.

## 3. Codex hisoboti bilan solishtirish

| Codex topilmasi | Holat |
|---|---|
| P0: `seed_db()` kontekstsiz | Tuzatildi — `create_app()` fabrikasi. Migratsiya bitta atomar amal, gunicorn `--preload`. |
| P1: terim `load_id` siz, ikki reys takrorlanadi | Tuzatildi — har terim aniq reysga bog‘langan. Test bor. |
| P1: vakolat to‘liq emas, bot rolni tekshirmaydi | Tuzatildi — xizmat qatlamida rol va brigada tekshiruvi (web va bot uchun umumiy). Test bor. |
| P1: tranzaksiyasiz, takroriy yuborish, update_id yo‘q | Tuzatildi — har amal `BEGIN IMMEDIATE` ichida, `client_uuid`, `update_id` jadvali. Testlar bor. |
| P1: tarozi tahriridan keyin nakladnoy eskicha | Tuzatildi — tuzatish faqat Rahbarda, sabab bilan; nakladnoy va Nayman farqi ham yangilanadi. Test bor. |
| P1: MAX+1 raqam poygasi | Tuzatildi — tranzaksiya ichidagi hisoblagich. Parallel test bor. |
| P1: rasmlar ochiq static, SW hammasini keshlaydi | Tuzatildi — rasm faqat login bilan va brigada tekshiruvi bilan beriladi; rasm Pillow bilan tekshirib qayta kodlanadi; SW faqat static fayllarni keshlaydi. |
| P1: WAL bazaga `cp` backup | Tuzatildi — backup API, integrity_check, tiklash testi. |
| P1/P2: NaN/inf, musbat summa, CHECK | Tuzatildi — tekshiruv va bazadagi CHECK cheklovlari. |
| P1/P2: SECRET_KEY/admin parol fallback | Tuzatildi — SECRET_KEY'siz ishlab chiqarishda ishga tushmaydi; admin paroli tasodifiy, birinchi kirishda almashtiriladi. |
| Auditda eski qiymat va sabab | Tuzatildi — old/new/reason/manba/IP; audit o‘zgartirilmaydi. |
| Brutto va tara alohida | Tuzatildi — ikki alohida bosqich, ikki vaqt, ikki operator bo‘lishi mumkin. |
| `sent = weighted` | Tuzatildi — jo‘natilgan = nakladnoylar; “yo‘lda” alohida, kamomad hisoblanmaydi. |
| Narx/qarz/kassa/ishchi haqi | Qilindi — narx noma’lum bo‘lsa “hisoblanmagan”, 0 emas. |
| PDF/Excel | Qilindi — nakladnoy va hisobotlar chop etiladi (PDF brauzer orqali), Excel (.xlsx) eksport. |
| Xarita, qidiruv, foto filtrlari | Qilindi. |
| Mavsum maydon tarixi | Qilindi — `field_seasons` jadvali. |
| Hero uchun skrinshot fonidan foydalanish | Tuzatildi — tasvirning faqat matnsiz paxta dalasi qismi olindi, siqildi; telefonda yuklanmaydi. |
| Netto ishchilarga qayta taqsimlanmasin | Amalga oshirildi — faqat haqiqiy dala kg ko‘rsatiladi. |
| TOLDI (dalada) va “Tortishni yakunlash” (tarozida) ajratilsin | Amalga oshirildi. |

## 4. Tekshirilmagan (bu muhitda imkoni yo‘q)

- Haqiqiy server, domen, HTTPS (Caddy) — server hali berilmagan.
- Haqiqiy Telegram API — bot tokeni yo‘q. Bot mantiqi soxta Telegram bilan to‘liq sinalgan.
- Haqiqiy telefon va real mobil tarmoq. Tezlik Chrome emulyatsiyasi bilan o‘lchandi.
- Xarita sun’iy yo‘ldosh plitkalari (sinov muhitida tashqi internet yopiq).
- Tarozi qurilmasidan avtomatik og‘irlik olish — tarozi modeli noma’lum, hozir qo‘lda kiritiladi.
- Azizbek ERP bilan jonli ulanish — ulanish ma’lumotlari hali berilmagan.
