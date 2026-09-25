# PAXTA — ish tarixi va joriy holat (keyingi sessiya uchun eslatma)

Oxirgi yangilanish: 2026-09-25. Yozgan: Claude (bulutdagi sessiya “Yangi loya qilash”).

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

Test loginlari (faqat test bazasi): `admin / Test2026!`; `juma`, `tarozi01`, `buxgalter`, `asadbek`, `rahbar` — `Demo2026!`.

## Bajarilmagan / kutilmoqda

- **Server (Hetzner) ga o‘rnatish** — `docs/DEPLOYMENT.md`, domen faol bo‘lmasa `tools/vaqtinchalik_https.sh`, keyin `tools/domen_ulash.sh`.
- **Haqiqiy telefon + Telegram bilan qabul sinovi** — `docs/QABUL_SINOVI.md` (18 qadam), server kerak.
- Telegram bot tokeni, arxiv kanali, Google Sheets xizmat akkaunti, rclone zaxira joyi — `docs/ARXIVLAR.md` (hammasi “ulanmagan”).
- Biznes ma’lumotlari: 9.8 ga farq (310.2 vs 320), dala konturlari, narx, ish haqi stavkasi, kassa boshlang‘ich qoldig‘i, 80%/5 kun qoidasi — `docs/OCHIQ_MASALALAR.md`.
- Azizbek ERP jonli ulanishi — tafsilotlar berilmagan (API tayyor: `docs/ERP_API.md`).

## Muhim qarorlar (nega shunday)

- Bir telashkaga bir vaqtda bitta tugallanmagan reys; terim reysga bog‘lanadi → ikki marta hisob yo‘q.
- Brutto va tara alohida bosqich; farq sozlamadagi % dan katta bo‘lsa sabab majburiy.
- Tortish tuzatishni faqat rahbar, sabab bilan; nakladnoy netto va PDF yangi versiya bilan yangilanadi.
- Ishchilarga netto avtomatik qayta taqsimlanmaydi (tasdiqlangan qoida yo‘q).
- Yopilgan mavsum maydonlari `field_seasons` da qotiriladi.
- Rasmlar yopiq (`/media`, login + brigada tekshiruvi), service worker faqat `/static/` ni keshlaydi.
- Telefon tezligi o‘lchangan: Slow 3G’da brigadir bosh sahifasi 33 KB, ~1.5 s.
