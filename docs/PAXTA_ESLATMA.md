# PAXTA — ish tarixi va joriy holat (keyingi sessiya uchun eslatma)

Oxirgi yangilanish: 2026-09-25 (kech, v2.5.0 — ikki bot). Yozgan: Claude (bulutdagi sessiya “Yangi loya qilash”).


## ⏸ TO‘XTATILGAN JOY — 04.10.2026 (tun), v2.31.0 (main = production’ga push qilingan, server olishi kutilgan)

Foydalanuvchi boshqa loyihaga o‘tdi. Davom ettirganda AVVAL saytdagi versiyani tekshir (pastda v2.31.0 bo‘lishi kerak), keyin
quyidagi 4 ishni bajar (har biri uchun foydalanuvchi “hammasini qil” deb ruxsat bergan; ruxsat so‘raydigan rejimda ishlaydi):
1. hosil-qabuli.uz jadvalini qayta o‘qib `/buxgalteriya/hosil-qabuli` ga CSV yuklash (oldin 26/29 yuklangan — 367843, 363442,
   362185 tushib qolgan edi; v2.29.2 da tuzatilgan). hosil-qabuli.uz da FAQAT O‘QISH — hech narsa bosilmaydi.
   Kirish har tab uchun alohida: Chrome dagi Claude guruhidagi tabda foydalanuvchi o‘zi kiradi.
2. “Klaster kg i bilan tuzatish”: 311058 → UY-0013 (2 500 → 3 080; Yunus “Dala jami bilan” bosgan; kombaynlar K-02 Orazboy,
   Farhod og‘a — xizmat); 289603 → UY-0008 + TL-2026-000038 (2 900 + 2 900 = 5 800; ikkalasini “Administrator” kiritgan).
3. “Xodimlarga Telegram’da yuborish” — Yunusga savol: 353283 (03.10 12:52, qo‘l, 1 270 kg) — TL-000059 (730, Gulbohar 22 ishchi)
   bilan qabul qilingan, rasmda UY-0024 qog‘ozi (brutto 7 960 − tara 6 690 = 1 270). Qolgan 540 kg: ikkinchi telashka yoki dalada
   kam yozilgan — Yunus/Gulbohar javobidan keyin 353283 ni tuzatish. UY-0024 sahifasini ochganda u “OCHIQ” bo‘lib qolgan bo‘lishi mumkin.
4. Sozlama `combine_prov_pay_pct` 90 → 0 (kombaynga faqat PQ-17 kg bo‘yicha pul). Auto rejimda brauzer orqali saqlash bloklangan edi.
Yana: 4 ta yangi PQ-17 PDF (PQ-2936/2939/2942/2944) ni foydalanuvchi yuklashi kerak; bugungi 363442 (qo‘l 1 800) va 362185
(kombayn 1 580) bizda yo‘q bo‘lishi mumkin — punktdan so‘rash.
Kunlik avtomatik tekshiruv: Claude ilovasidagi scheduled task `kunlik-paxta-sverka` (har kuni ~21:07).
Holat 03.10 kech: hosil jadvali 26 yukdan 22 ✓ mos; 13 reysga yuk xati № yozildi (359608→UY-0026 va boshqalar).

## QO‘SHIMCHA 03.10.2026 (kech) — v2.29.0

- Sabab topildi: 3 ta PQ-17 bog‘lanmadi, chunki punktda yuk xati № yozilmagan va punkt kg o‘rniga dala kg qolgan
  (TL-000038, TL-000059, UY-0013 da farq 0). Endi punkt qabulida yuk xati № majburiy (`punkt_require_load_no`, testlarda 0),
  bir raqam ikki reysga yozilmaydi (`pq17.clean_load_no`).
- hosil-qabuli.uz Excel → `/buxgalteriya/hosil-qabuli` (`surxon/hosil.py`, jadval `hq_loads`, sxema 22): mos / kg farq /
  raqamsiz (bir tugma bilan raqam yoziladi) / bizda yo‘q. Jadval sanasi sanasiz PQ-17 ga qo‘yiladi.
  Haqiqiy Excel namunasi bilan tekshirilmagan — sarlavha kalit so‘zlar bo‘yicha topiladi.
- FAYZ: 353717→TL-000061, 322034→TL-000043 biriktirildi. Ochiq: 289603 (5 800), 311058 (3 080), 353283 (1 270).

## QO‘SHIMCHA 03.10.2026 — v2.28.0 (main’da, production’ga “tasdiqlayman” kutilmoqda)

- FAYZ AGROKLASTER sverkasi tekshirildi: 25 yuk, netto 50 040, konditsion 47 605, summa 375 570 775 — hosil-qabuli.uz bilan mos.
- PQ-17 sanasi: yangi (XI…) shakllarda sana o‘qilmay “—” chiqardi → `pq17.parse_date` raqamlarni har xil bo‘linishda o‘qiydi;
  baribir topilmasa bog‘langan reysning punkt sanasi qo‘yiladi (`refresh_dates`, sahifa ochilganda).
  Haqiqiy XI PDF namunasi bilan tekshirilmagan — serverdagi saqlangan PDF lar sahifa ochilganda qayta o‘qiladi.
- Bog‘lanmagan PQ-17 uchun taklif faqat kg ±1% bo‘lsa (oldin 5 800 kg ga 2 9xx kg reys taklif qilinardi).
- Bitta yuk xatida bir nechta reys: `pq17.link_many` → “PQ-<kod>” nomli umumiy yuk (QABUL), PQ-17 kg/summasi reyslarga bo‘linadi.
- Dalalar xaritasi PDF (A4): `/admin/dalalar/xarita.pdf` (`?dala=` / `?brigadir=`), `surxon/atlas.py`, Esri tile’lar
  `UPLOAD_DIR/tiles` da keshlanadi (47 dala: birinchi marta ~1 daqiqa, keyin ~8 s).

## QO‘SHIMCHA 27.09.2026 (kunduzi) — v2.18.3 production’da

- Texnika + zapravka QR: Admin → Texnikalar → “QR kodlar (A4, har biri alohida)” (`/admin/texnikalar/qr.pdf`, ?turi=).
  Traktor/kombayn = solyarka QR (SPX-YQ:T), zapravka = SPX-YQ:Z, pritsep = `https://domen/tq/<token>` (joriy reysni ochadi).
- Odamlar va texnika bitta xaritada: dashboard kartasi + `/rahbar/xodimlar` (Odamlar/Texnika qatlamlari, to‘liq ism yorlig‘i).
- Kuzatuv → Odamlar: admin odamga rasm qo‘yadi (Telegram rasmi o‘rniga). Joylashuv 1 soat kelmasa bot bir marta eslatadi
  (sozlama `staff_live_remind`).
- Foydalanuvchi ertaga traktorlarga GT06 o‘rnatadi → Admin → Texnikalar’da IMEI bog‘lash + GT06 SMS sozlash (server IP, port 5023).

## HOLAT 27.09.2026, 04:40 (ENG OXIRGI — “paxta” deyilsa shu yerdan davom eting)

**Server yangilandi va hammasi ulandi:**
- Server = v2.18.1 (health ok), avtomatik yangilash yoqilgan (systemd timer, har 5 daqiqa, `production` ni kuzatadi).
  GitHub `production` = 491a72c. Endi Console kerak emas: main → (foydalanuvchi “tasdiqlayman”) → `git push origin main:production`.
- Hetzner Console’da `:` belgisi noto‘g‘ri yoziladi — buyruqlarda `https://` ishlatmang (`curl -sL surxan-paxta.uz/health`).
- Narxlar kiritildi: qo‘l 7800, kombayn 7600 → punktdan kutilayotgan 18 202 000 so‘m (2350 kg). Reyslar to‘g‘ri (faqat 2 ta haqiqiy).
- Punkt Nayman-1 koordinatasi saqlandi (admin/punktlar).
- Telegram: bot @surxan_paxta_bot yangi “PAXTA_SURXAN” guruhida admin, “Ishchi guruh” qilindi. 6 kishi ulangan.
  Agronom/brigadir/haydovchilarni shu guruhga qo‘shish qoldi (foydalanuvchi o‘zi qo‘shadi).
- TV yoqildi, TV kaliti yaratildi (“Ofis televizori”), /tv ishlayapti.
- Google xarita kaliti O‘CHIRILDI (Google kulrang chiqdi + pulli) — bepul Esri ishlaydi.
  `static/js/basemap.js` tuzatildi (Google tile haqiqatan yuklansagina o‘tadi) — main’da (4b576ac), production’da HALI YO‘Q.
- Google Sheets ULANDI (loyiha bycotton-tizim, surxon-sheets@bycotton-tizim.iam.gserviceaccount.com, jadval “SURXAN-PAXTA”,
  ruxsat “Доступ ограничен”). Sinov o‘tdi. Tizim “SPX …” varaqlarini yaratdi: KASSA KIRIM-CHIQIM, PAXTA-PUNKT,
  XARAJATLAR, TERIMCHILAR, Sinov. (Birinchi JSON kalit chatga tushib qolgan edi — o‘chirildi, yangisi yuklangan.)

**KEYINGI QADAM (foydalanuvchi “paxta” / “davom etamiz” desa):**
1. Foydalanuvchining “Umumiy hisob” varag‘i eski varaqlardan (Pul harakati, Reyslar, Nayman…) o‘qiydi → o‘zi to‘lmaydi.
   Foydalanuvchi “SPX KASSA KIRIM-CHIQIM” va “SPX PAXTA-PUNKT” varaqlarining rasmini yuboradi → “Umumiy hisob” uchun
   SPX varaqlardan o‘qiydigan yangi formulalar yozib berish (sheets.new emas, mavjud jadval; tizim qo‘lda varaqlarga tegmaydi).
2. Task #38: dashboard/TV/telefon dizayni (Codex rasmi) — real ma’lumot bilan, skrinshot → “tasdiqlayman” → production.
3. basemap.js tuzatishi keyingi reliz bilan production’ga.
4. Keyin: GT06 trekerlar (sotib olinganda SMS sozlash), kamera (pulli — alohida kelishuv).

## HOLAT 27.09.2026 (kechroq bo‘lim — yuqoridagi yangiroq)

- GitHub `main` = `production` = v2.18.1 (152 test). Serverda hali v2.14.0 — foydalanuvchi Console’da yangilaydi
  (Hetzner akkaunt paroli muammosi: 10 daqiqa bloklangan, "Forgot password" tavsiya qilindi).
- Birinchi yangilashda `bash tools/install-autodeploy.sh` ham ishga tushiriladi → keyin server `production` ni o‘zi kuzatadi.
  **Qoida:** `production` ga faqat foydalanuvchi “tasdiqlayman” degandan keyin push (`git push origin main:production`).
- v2.17–2.18.1 da qilinganlar: terimlar 1-2-3, terilgan joy, internetsiz ishlash, punkt ETA, xodimlar xaritada, GT06 GPS
  (texnika, salarka me’yori, ish turi, dalalar tarixi), jonli kuzatuv (bot orqali guruhlardan rasm/video, eslatma, TV lenta),
  bekorlar hech qayerda hisoblanmaydi (+ startda himoya), dashboard Bugun/Kecha/Mavsum, reys bo‘yicha kg va pul,
  planshet, nakladnoy 2 nusxa + punkt to‘ldiradigan joy + tasdiqlangan nakladnoy (elektron muhr, QR /tekshir),
  punkt bilan hisob (qo‘l 7800 / kombayn 7600 — Buxgalteriyada o‘zgartiriladi, hech narsa qotmaydi).
- Serverdan keyin foydalanuvchi kiritadi: paxta narxlari, punkt koordinatasi, Google Maps kalit, Google Sheets.
- Keyingi ish: Dashboard/TV dizayni Codex rasmi bo‘yicha (docs/DIZAYN_MALUMOTLAR.md), GT06 treker sotib olingach SMS sozlash.

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

## 2026-09-26 KUNDUZI — OXIRGI HOLAT (“paxta” deganda shundan boshlang)

- Server: **v2.14.0** ishlayapti (/health tasdiqlandi). GitHub’da **v2.14.1** (zaxira sahifasida sana + tashqi nusxa holati,
  TV izohi) — keyingi yangilashda qo‘yiladi. Yangilash yo‘li: Hetzner Console → server → `>_` → root (parolni foydalanuvchi
  o‘zi yozadi; chatga so‘ramang) → `cd /opt/surxan-paxta.uz` → `docker compose exec -T app flask --app app backup` →
  `git pull --ff-only` → `docker compose up -d --build` → /health. Console’da `&&` yozilmaydi — buyruqlar alohida.
- Himoya ✅: kunlik zaxira (30 kun) + Storage Box u676951 (Helsinki, “Sinov muvaffaqiyatli”, zaxira xabarida
  “Serverdan tashqariga nusxalandi”) + Hetzner Backups yoqilgan + Delete/Rebuild Protection yoqilgan.
  (Backups keyin server → Backups bo‘limida “Disable backups” turganini bir marta tasdiqlash — ro‘yxatda belgi ko‘rinmay qoldi.)
- Root parol eski (rasmlarda ko‘ringan) — foydalanuvchi almashtirmadi; vaqti bo‘lsa `passwd` taklif qilish.
- 26.09 kunduzi haqiqiy terim BOSHLANDI. Test reyslar (25.09) hali bekor qilinmagan → kechqurun: Telashkalar → test reys →
  “Reysni to‘liq bekor qilish” (sabab “Sinov”), solyarka/kassa testlari “Bekor” → Direktor paneli → Mavsum = faqat haqiqiy ish.
  Bugungi haqiqiy reyslarni ADASHIB bekor qilmaslik: sana va reys raqamiga qarab tanlash.
- Keyin: UptimeRobot (bepul, /health), Google xarita kaliti, Google Sheets, Hayitvoy bilan solyarka (avval tiket), TV kaliti
  (Integratsiyalar → TV → Yoqish → kalit), mavsum rejasi (Sozlamalar → season_target_kg).

## HOZIR QAYERDA TO‘XTADIK (keyingi qadam)

1. 2026-09-26: serverda v2.14.0 (TV + Storage Box sahifasi) — foydalanuvchi Hetzner Console (>_) orqali root bilan o‘zi
   o‘rnatdi. Yo‘l: Hetzner → server → Rescue → Reset root password → Console; `&&` belgisi Console’da yozilmaydi
   (Shift+7 → 7), buyruqlar alohida-alohida beriladi. Brauzerdagi Codex’da SSH kaliti yo‘q. Faqat-o‘qish yozuvlar soni jadvali hali olinmagan.
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
3. ✅ BAJARILDI 2026-09-26: Storage Box u676951 (BX11, Helsinki) Integratsiyalar’da ulandi, “Sinov muvaffaqiyatli”. Storage Box (sotib olingan) ni **Admin → Integratsiyalar** dan ulanadigan qilish (SFTP host/login/parol
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

## Holat — 28.09.2026 (v2.22.1 saytda)
Qo‘llanmalar: `docs/qollanmalar/Qollanma_Punkt.pdf`, `docs/qollanmalar/Qollanma_Dala_hisobchisi.pdf` (namunaviy ma’lumot bilan).

Foydalanuvchi qilishi kerak:
- PA-000009: punkt kg 1 330 → 1 130 (Nakladnoy → “Qabulni ko‘rish / tuzatish”, sabab “PQ-17 bo‘yicha”).
- Blanklar: 600 ta yaratilgan (PB-0001…). Punkt endi blank raqami + rasmisiz qabulni yopmaydi
  (vaqtincha o‘chirish: Sozlamalar → punkt_blank_required = 0). Eski reyslar tartib bilan: PB-0001 → PA-000003,
  PB-0002 → PA-000007, PB-0003 → PA-000008, PB-0004 → PA-000009, PB-0005 → PA-000010, PB-0006 → PA-000011.
- Buxgalter: Agrobank (kredit + “To‘lovlar hisoboti” Excel, jami 1 395 234 357 so‘m bo‘lishi kerak), MK Leasing firmalari.
- Google Sheets “Umumiy hisob” varag‘iga SPXUMUMIY dan VLOOKUP formulalar (chatda berilgan).

Keyingi ishlar: Agrobank grafigi, MK Leasing shartnomalari, bank ko‘chirmasi importi, fakturalar, “Umumiy hisob” formulalari,
blank PDF ni 50 tadan bo‘lish (foydalanuvchi hozircha kerak emas dedi), TZST CE-220 qaysi kombayn (K-01/K-02).
