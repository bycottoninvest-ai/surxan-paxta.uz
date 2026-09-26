# PAXTA — ish tarixi va joriy holat (keyingi sessiya uchun eslatma)

Oxirgi yangilanish: 2026-09-25 (kech, v2.5.0 — ikki bot). Yozgan: Claude (bulutdagi sessiya “Yangi loya qilash”).

## Foydalanuvchi

- Ism: Azizbek (GitHub: `bycottoninvest-ai`). O‘zbek tilida yozadi, dasturchi emas — oddiy, qadamma-qadam tushuntirish kerak.
- Kompyuter: Windows 11, loyiha papkasi **`D:\loyihalar`**. Python 3.13.15 o‘rnatilgan (`py -3` ishlaydi).
- Kompyuterda ZIP orqali yuklab olingan nusxa: `D:\loyihalar\surxan-paxta.uz-main` (git EMAS). Lokal Claude ishlasa, yangilash uchun `git clone https://github.com/bycottoninvest-ai/surxan-paxta.uz D:\loyihalar\surxan-paxta.uz` qilib, git bilan ishlash yaxshiroq (`data-test/` ni kerak bo‘lsa ko‘chirib oling).
- Brauzerida ochiq tablar: Hetzner server paneli, “Surxan server”, sayt.uz “Domenlarim”, Google Sheets “SURXAN-PAXTA”. Demak **Hetzner server** va **Google jadval** tayyorlanayotgan bo‘lishi mumkin — so‘rang.
- Brauzerida “ChatGPT brauzerni boshqaryapti” ogohlantirishi ko‘rindi — kerak bo‘lmasa o‘chirishni maslahat berilgan.
- Boshqa loyihasi: `bycottoninvest-ai/azizbek-ai-assistant` (AI yordamchi, Telegram/ovoz) — alohida.

## Loyiha

SURXON TAXIATOSH TEXTILE uchun paxta mavsumini to‘liq yuritish tizimi, domen **surxan-paxta.uz** (hali faollashmagan). Manba: foydalanuvchi ChatGPT’da tayyorlagan dastlabki kod + 8 ta dizayn rasmi + Codex tahlili. Dizayn saqlangan (ko‘k-oq, SURXON logosi, paxta banneri).

Zanjir: dala → qo‘l/kombayn terimi → telashka reysi → TOLDI (rasm) → umumiy tarozi (brutto, keyin tara) → netto → PA-000001 nakladnoy + PDF → Nayman qabuli → to‘lov → ishchi haqi/avans/xarajat/kassa → hisobot.

Ko‘rinishlar (bitta backend, bitta baza): kompyuter dashboardi; telefon — rolga mos yengil bosh sahifa (3–4 katta tugma, “Boshqa” menyu); Telegram bot; `/tv` (faqat ko‘rish kaliti, pul/ismsiz); Azizbek ERP uchun faqat o‘qish API (`/api/erp/v1`).

Rollar: admin, rahbar, brigadir (faqat o‘z brigadasi), tarozi, buxgalter, kassa (Asadbek), terimchilar hisobchisi, haydovchi — `docs/VAKOLATLAR.md`.

## Bajarilgan (GitHub `main`)

1. To‘liq tizim (Flask + SQLite WAL, 38 test) — commit `e5a1a10`.
2. Server tomonida nakladnoy PDF (Nayman nusxasi narxsiz + ichki nusxa, versiyalar), outbox (Telegram arxiv kanali, Google Sheets), rclone mustaqil zaxira, `flask smoke-check`, test rejimi, `start_test.bat`, vaqtinchalik HTTPS va domenga o‘tish skriptlari — `e24b4ab`.
3. Windows tuzatishlari: Store python yorlig‘ini aniqlash (`0b5fdb9`), `tzdata` + UTC+5 zaxira (`67701b4`).
4. **Foydalanuvchi kompyuterida test rejimi ishga tushdi** (2026-09-25): dashboard, sun’iy yo‘ldosh xaritasi, ob-havo ishladi. Telefon manzili: `http://192.168.100.66:5000` (Wi-Fi IP o‘zgarishi mumkin). Telefonda login sahifasi ochildi.
5. **v2.1.0** — telefon tuzatishlari: toza login (faqat “TEST REJIMI · v2.1.0” belgisi), terim formasi soddalashdi, takror kg ogohlantirishi faqat shubhada chiqadi, JS xatolari tuzatildi, `tests/test_frontend.py` (jami 42 test). Playwright telefon emulyatsiyasida tekshirildi (ikki marta bosish = 1 yozuv, oflayn navbat = 1 yozuv).
6. **v2.2.0 — soddalashtirilgan ish tartibi (Azizbek qarori, 2026-09-25):**
   - Brigadirlarda telefon yo‘q → brigadirlarga login berilmaydi, ular faqat ism sifatida (brigada) turadi.
   - Terimni **4 ta “Hisobchi (terim)”** yozadi: Asadbek, Mirjalol, Sadokat, Gulbohar (uchala brigada uchun). Admin: Azizbek.
   - Ish haqi: qo‘l terimi **1500 so‘m/kg**, kombayn ham **1500 so‘m/kg** (Sozlamalarda o‘zgartiriladi; `worker_rate_hand`, `combine_rate`).
   - **Faqat qo‘l terimi bo‘lgan telashka:** hisobchi har odamni dala tarozisida tortib yozadi → “Tugatish” → nakladnoy **dala kg yig‘indisi** bilan avtomatik chiqadi, telashka yopiladi (`auto_waybill_hand=1`, `weighings.basis='dala'`).
   - **Kombayn bor telashka:** katta tarozida yoki Nayman punktida tortiladi (brutto/tara) → nakladnoy.
   - Katta tarozi va pulni (kassa) kim yozishi hali so‘ralmagan — hozircha admin.
   - Server: Hetzner `surxan-paxta`, IP 88.198.122.72, o‘rnatish: `tools/server_ornatish.sh` (hali ishga tushirilmagan).
7. **v2.3.0 — daladan punktgacha (Azizbek topshirig‘i, 2026-09-25)**, batafsil: `docs/PUNKT.md`:
   - Telashka raqami avtomatik: `TL-YYYY-NNNNNN` (`trailer_loads.trip_no`, `counters` → `trip-YYYY`), o‘zgarmaydi.
   - TUGATISH: lock, “Punkt uchun nakladnoy” (QR bilan, ism/narx yo‘q) va “Ichki terim hisoboti” (har odam kg) PDF, status PUNKTGA YO‘LDA.
   - Punktlar (`stations`), rol `station` (“Punkt operatori”, masalan Yunus → Nayman-1), faqat `/punkt` ekranlari (server tomonida `station_gate`).
   - Punkt: QR / raqam / ro‘yxat orqali topish, KELDI, punkt tarozisi, farq (≤1% normal, ≤3% diqqat, >3% katta), sabab tugmalari, QABUL QILINDI (takrorlanmaydi).
   - Hisobotlar: “Punktlar: dala va punkt farqi”, “Farq sabablari”. Elektron tarozi uchun `surxon/scale.py` (hozir manual).
   - Baza v4. Yangilanishdan oldin avtomatik nusxa olinadi: `*.oldin-vN-*.bak`. Checkpoint commit: `1eb2ecc`.
   - Test rejimi loginlari: `mirjalol` (hisobchi), `yunus` (punkt) / `Demo2026!`.
   - Rasmlardagi “BYCOTTON” brendi ishlatilmagan, logotip SURXON TAXIATOSH.
8. **v2.4.0 — Buxgalteriya va kassa (2026-09-25)**, batafsil: `docs/BUXGALTERIYA.md`:
   - Baza v5: `harvests.rate/rate_unit/amount` (narx tarixi), `cashboxes`, `payouts` (TAYYOR→BERILDI), `combine_work`, `cash_days`, `debts`, INC/EXP/PAY/ADJ/DEB raqamlar.
   - Kod: `surxon/accounting.py` (qoidalar), `surxon/reporting.py` (hisobot, Telegram kunlik hisobot), `surxon/views/acct.py` (/buxgalteriya, /kassir).
   - Rollar: buxgalter — hammasi moliyaviy; kassir — faqat tayyor to‘lovlar + o‘z kassasi (server gate); hisobchi/punkt — moliya yopiq.
   - Sheets upsert (ID bo‘yicha, dublikat yo‘q); Telegram hisobot kanali `TELEGRAM_REPORT_CHAT_ID` (+ ixtiyoriy `TELEGRAM_REPORT_BOT_TOKEN`).
   - ERP API: /payouts, /worker-balances, /combines, /debts, /cash-days (+ doc_no, rate, amount).
   - Test rejimi: buxgalter / asadbek (kassir) / Demo2026!.
9. **Server ulanishlari tayyorlandi (kod, 69 test):** `tools/sozlash.sh telegram|sheets|sheets-sinov|zaxira|narx|holat` (sirlar faqat serverda, ko‘rinmay kiritiladi), `flask backup-verify [--offsite]` (alohida bazaga tiklab solishtiradi), `flask holat` (ISHLAYDI/ULANMAGAN/TEKSHIRILMAGAN/XATO), `flask sheets-inspect` (faqat o‘qish), `flask sheets-sinov` (1 000 so‘m sinov → qayta yuborish → bekor), `SPX UMUMIY` jadvali (dashboard formulalari uchun ID-li jami ko‘rsatkichlar). Tizim faqat `SPX ` varaqlariga yozadi, qo‘lda qilingan varaq/formulalarga tegmaydi. Batafsil: `docs/SERVER_ULASH.md`.
10. **Narxlar (Azizbek):** qo‘l 1 500 so‘m/kg, kombayn 1 500 000 so‘m/tonna. 250 000 — faqat eski sinov, endi hech qayerda yo‘q.
11. **Google Sheets:** https://docs.google.com/spreadsheets/d/1eWl21webrxSAV_NDdvv8dX1lWmu5gSEMSD1fvMWh8Mw — “Umumiy hisob”, “Ishchilar” va boshqa varaqlar bor. Talab: ularni SPX ma’lumotlariga formulalar bilan bog‘lash (ustun/ID/sana/birlik tekshiruvi), sinov yozuvida dashboard yangilanishini ko‘rsatish, qayta yuborishda ikki marta hisoblanmasin.

## v2.5.0 — ikki Telegram bot (2026-09-25 kech)
Azizbek topshirig‘i: 1) hisobot kanali — ma’lumot doim tushib turadi va saqlanadi, hech kim yozolmaydi;
2) kuzatuv — tizim odamlardan (traktorchi, brigadir, agronom) rasm/video so‘raydi, javob rahbar dashboardida ko‘rinadi.
- Batafsil: `docs/KUZATUV.md`. Sahifa: `/kuzatuv` (Rasm/video, Odamlar, Jadval), bosh sahifada “Kuzatuv (bugun)”.
- Baza v6: `tg_chats`, `tg_members`, `media_rules`, `media_requests`, `media_items`. Kod: `surxon/kuzatuv.py`,
  `surxon/telegram_bot.py` (guruh, `K-` havola, rasm/video), `surxon/views/kuzatuv.py`. Worker har ~30 s `kuzatuv.tick()`.
- Hisobot kanaliga `reporting.feed()`: reys tugadi, punkt qabul, har bir kassa harakati, kuzatuv kechikdi.
- 77 test o‘tadi. Haqiqiy Telegram bilan sinalmagan (token serverda kiritilgach).

## 2026-09-25 kech — real ishga chiqish (Codex serverni o‘rnatdi)
- Server: https://surxan-paxta.uz ishlaydi (Codex: fcc08a0 o‘rnatilgan). Admin paroli almashtirilgan. Bot webhook ishlaydi.
- Yangi: Admin → **Xodimlar va loginlar** (rol yonida ism → login + bir martalik parol); telefonda **nakladnoy PDF: Ko‘rish / Ulashish / Yuklab olish**
  (printer yo‘q; Web Share bilan fayl Telegramga); **Telegram kanallari** Admin → Integratsiyalar'da tanlanadi (getUpdates yo‘q).
- Codex qabul misoli `tests/test_acceptance.py` da (360 000 / 210 000 / 830 000 / −2 kg) — o‘tadi.
- Xodimlar (Azizbek tasdiqladi): Rahbar — Salayev Aziz; Hisobchi — Asadbek, Mirjalol, Sadokat, Gulbohar; Punkt — Yunus;
  Buxgalter+kassir — Ibdulayev Asadbek (Asadbek bilan bir odam, lekin alohida login). Tarozi xodimi yo‘q.
- To‘siq: dalalar kiritilmagan (nomi/gektar/brigada Azizbekdan). Bir reys = bir dala (aralashtirilmaydi).
- Kompyuterdagi Wi-Fi router DNS (192.168.100.1) domenni topmagan edi → kompyuterga 1.1.1.1/8.8.8.8 qo‘yildi.

## 2026-09-26 — v2.8.0 … v2.12.2 (sayt ishlayapti: https://surxan-paxta.uz)

- v2.8.0 punkt (brutto/tara, farq sababi, QR) · v2.9.0 buxgalter telefoni `/hamyon` (tekshir → tasdiq, tuzatish)
- v2.10.0 solyarka `/yoqilgi` (rol “Yoqilg‘i mas’uli” — Hayitvoy; zapravka/tiket/QR yorliqlar; berishda kamera rasmi)
- v2.11.0 dalalar KML/GeoJSON import (57 dala haqiqiy bazaga saqlangan), xaritada chizish; v2.11.1 SW kesh tuzatish
- v2.11.2 Google xarita kaliti Admin → Integratsiyalar’da · v2.12.0 Direktor paneli `/rahbar` (telefon, faqat ko‘rish)
- v2.12.1 Google Sheets’ni Integratsiyalar’dan ulash (havola + service account .json)
- v2.12.2 Admin: “Reysni to‘liq bekor qilish” (tortish + nakladnoy + punkt qabuli, sabab bilan, o‘chirmasdan)
- v2.13.0 yangi TV dashboard `/tv` (foydalanuvchi dizayni bo‘yicha, pulsiz; mavsum rejasi: season_target_kg) — GitHub’da, serverga hali qo‘yilmagan
- Rollar bo‘yicha rasmli PDF qo‘llanmalar: `docs/qollanma/` (hisobchi, punkt, buxgalter, kassir, yoqilg‘i, direktor, admin)

## HOZIR QAYERDA TO‘XTADIK (keyingi qadam)

1. Serverda v2.12.2 ishlayapti (/health tasdiqlandi). v2.13.0 (TV) ni Codex bilan qo‘yish kerak — qadamlarni birma-bir
   so‘rash (uzun xabarda Codex qotib qoldi). Faqat-o‘qish yozuvlar soni jadvali hali olinmagan.
2. Ertalab haqiqiy ish boshlanadi: avval Admin → Zaxira → “Hozir zaxira olish”, keyin test reys(lar)ni
   “Reysni to‘liq bekor qilish” bilan, solyarka/kassa sinovlarini “Bekor” bilan tozalash → direktor paneli 0.
   Test nakladnoy PA-000001 ni olgan — raqamlar qayta ishlatilmaydi (ataylab).
3. Google xarita kaliti (loyiha bycotton-tizim, “surxan-xarita”) — Integratsiyalar’ga kiritilishi kerak.
4. Google Sheets: service account yaratish → .json → Integratsiyalar → jadvalni email’ga “Editor” → Sinov.
   Jadval: 1eWl21webrxSAV_NDdvv8dX1lWmu5gSEMSD1fvMWh8Mw (tizim faqat SPX… varaqlariga yozadi).
5. Solyarka: zapravka qo‘shilgan, QR yorliqlar chop etilgan; tiket ochish va Hayitvoy bilan birinchi sinov qoldi.
6. TV dashboard: foydalanuvchi o‘z dizaynlarini olib keladi → `/tv` ni shu dizayn bo‘yicha qayta qurish.
7. Direktor panelining keyingi qismi: HOZIR filtri, muammolar markazi (Yangi/Ko‘rilmoqda/Yopilgan), texnika sahifasi (TR-07).

## ERTAGA BIRINCHI NAVBATDA — server ishonchliligi (foydalanuvchi so‘radi, unutmang!)

Hozir: `restart: unless-stopped` + har kecha zaxira (30 kun) — lekin zaxira O‘SHA serverda. Server yo‘qolsa, hammasi yo‘qoladi.
1. Foydalanuvchi Hetzner Console’da: server → **Delete/Rebuild protection** yoqish (bepul).
2. Hetzner **Backups** (server narxining 20 %, kunlik, 7 kun) — pullik, foydalanuvchi qaror qiladi; tavsiya qilingan.
   Katta yangilanishdan oldin **Snapshot**.
3. ✅ v2.14.0 da qilindi (serverga qo‘yish kerak): Storage Box (sotib olingan) ni **Admin → Integratsiyalar** dan ulanadigan qilish (SFTP host/login/parol
   sahifada, parol faylda 600, ekranda ko‘rinmaydi) → har kecha zaxira serverdan tashqariga. v2.13.0 bilan birga deploy.
4. UptimeRobot (bepul) → `https://surxan-paxta.uz/health` har 5 daqiqa, Telegram/email ogohlantirish — qadamma-qadam yozib berish.
Maqsad: ma’lumot 3 joyda — server, Hetzner Backups, Storage Box.

## Bajarilmagan / kutilmoqda

- Serverdan tashqari zaxira (Hetzner Storage Box sotib olingan) — `bash tools/sozlash.sh zaxira`, root parol kerak;
  osonroq yo‘l (Integratsiyalar’dan ulash) hali qilinmagan.
- Test ishchi ismlarini yashirish (kerak bo‘lsa).
- Biznes ma’lumotlari — `docs/OCHIQ_MASALALAR.md`. Azizbek ERP jonli ulanishi — `docs/ERP_API.md`.
- Deploy faqat Codex orqali (Claude SSH qila olmaydi): har versiyada `docs/CODEX_YANGILASH.md` + Codex xabari.

## Muhim qarorlar (nega shunday)

- Bir telashkaga bir vaqtda bitta tugallanmagan reys; terim reysga bog‘lanadi → ikki marta hisob yo‘q.
- Brutto va tara alohida bosqich; farq sozlamadagi % dan katta bo‘lsa sabab majburiy.
- Tortish tuzatishni faqat rahbar, sabab bilan; nakladnoy netto va PDF yangi versiya bilan yangilanadi.
- Ishchilarga netto avtomatik qayta taqsimlanmaydi (tasdiqlangan qoida yo‘q).
- Yopilgan mavsum maydonlari `field_seasons` da qotiriladi.
- Rasmlar yopiq (`/media`, login + brigada tekshiruvi), service worker faqat `/static/` ni keshlaydi.
- Telefon tezligi o‘lchangan: Slow 3G’da brigadir bosh sahifasi 33 KB, ~1.5 s.
