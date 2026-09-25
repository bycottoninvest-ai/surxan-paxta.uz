# Tashqi arxivlar: holati va ulash

Holatni ko‘rish: **Admin → Sozlamalar → Integratsiyalar → “Tashqi arxivlar holati”**. Serverda: `docker compose exec app flask --app app smoke-check`.

Holat belgilari halol ko‘rsatiladi:

| Belgi | Ma’nosi |
|---|---|
| **Ulanmagan** | Sozlama yo‘q. Yozuvlar navbatda saqlanadi va ulangandan keyin yuboriladi |
| **Sozlangan, hali tekshirilmagan** | Sozlama bor, lekin hali birorta muvaffaqiyatli yuborish bo‘lmagan |
| **Ishlayapti** | Oxirgi haqiqiy yuborish muvaffaqiyatli bo‘lgan (vaqti ko‘rsatiladi) |
| **Xato** | Oxirgi urinish xato bilan tugagan (xato matni ko‘rsatiladi). Qayta urinish avtomatik |

## Joriy holat (2026-09-25)

| Arxiv | Kod | Haqiqiy ulanish |
|---|---|---|
| Nakladnoy PDF (serverda, avtomatik, versiyalar bilan) | ✅ tayyor, testlangan | ✅ ishlaydi (tashqi xizmat kerak emas) |
| Yopiq Telegram arxiv kanali (PDF + reys rasmlari) | ✅ tayyor, soxta Telegram bilan testlangan | ❌ **ulanmagan** — bot tokeni va kanal ID si yo‘q |
| Telegram hisobot kanali (kunlik hisobot, ogohlantirishlar) | ✅ tayyor, testlangan | ❌ **ulanmagan** — kanal ID si yo‘q |
| Google Sheets nazorat nusxasi | ✅ tayyor, soxta Sheets bilan testlangan | ❌ **ulanmagan** — Google xizmat akkaunti va jadval yo‘q |
| Mustaqil (serverdan tashqari) zaxira | ✅ tayyor (rclone) | ❌ **ulanmagan** — zaxira joyi (bulut) tanlanmagan |
| Serverdagi kunlik zaxira | ✅ tayyor, tiklash testlangan | ⏳ server ishga tushgach ishlaydi |

## 1. Yopiq Telegram arxiv kanali

1. Telegram'da **yopiq kanal** (Private channel) yarating, masalan “SURXON arxiv”.
2. Botni kanalga **admin** qilib qo‘shing (“Post messages” ruxsati bilan).
3. Kanal ID sini oling: kanalga biror xabar yozing, uni @userinfobot ga forward qiling. ID `-100…` bilan boshlanadi.
4. Serverdagi `.env` ga yozing: `TELEGRAM_ARCHIVE_CHAT_ID=-100…`, keyin `docker compose up -d`.
5. Integratsiyalar sahifasida **Sinov** tugmasini bosing. Kanalga sinov xabari kelishi va holat **Ishlayapti** bo‘lishi kerak.

Kanalga avtomatik yuboriladi:
- har nakladnoyning Nayman nusxasi va ichki nusxasi (PDF, har versiya);
- reys bilan bog‘langan har bir rasm: TOLDI, tarozi, Nayman va boshqalar.

Oldin navbatda to‘plangan yozuvlar ham yuboriladi.

## 2. Google Sheets nazorat nusxasi

1. Google Cloud'da xizmat akkaunti (Service Account) yarating va Google Sheets API ni yoqing. JSON kalitni yuklab oling.
2. Yangi Google jadval yarating, unda **Nakladnoylar**, **Tolovlar** va **Sinov** nomli varaqlar ochish kerak. Jadvalni xizmat akkauntining email manziliga **Editor** qilib ulashing.
3. JSON faylni serverdagi `data/google-service-account.json` ga joylang. `.env` ga yozing: `GOOGLE_SHEETS_ID=<jadval URL idagi ID>`.
4. `docker compose up -d` → Integratsiyalar → **Sinov**.

Jadvalga yoziladigan qatorlar (faqat qo‘shiladi, o‘chirilmaydi):
- nakladnoy yaratildi, tarozi tuzatildi, Nayman qabuli, bekor qilindi;
- to‘lov kiritildi yoki bekor qilindi.

Har qatorda voqea ID si bor. Bu nusxa **nazorat uchun**, asosiy hisob tizimning o‘zida yuritiladi.

## 3. Mustaqil (serverdan tashqari) zaxira

1. Zaxira joyini tanlang: Backblaze B2, Google Drive, Yandex Disk, S3 yoki boshqa SFTP server.
2. Serverda sozlang: `docker compose run --rm backup rclone config --config /data/rclone.conf` va remote yarating (masalan `b2`).
3. `.env` ga yozing: `OFFSITE_RCLONE_REMOTE=b2:surxon-zaxira` va `RCLONE_CONFIG=/data/rclone.conf`.
4. `docker compose up -d` → Integratsiyalar → **Sinov**.

Har tungi zaxiradan keyin yangi fayllar nusxalanadi. `rclone copy` masofadagi fayllarni o‘chirmaydi. Iloji bo‘lsa, versiyalash yoqilgan bucket va faqat yozish huquqli kalit ishlating.


## Telegram hisobot kanali (faqat o‘qish)

Bu kanal Azizbek loyihasidan alohida, faqat SURXAN-PAXTA.UZ uchun. Unga odamlar yozmaydi — tizim yuboradi.

1. Telegram'da **yopiq kanal** yarating, masalan “SURXAN-PAXTA hisobot”. Rahbar va buxgalterni obunachi qiling.
2. Botni kanalga **admin** qiling. Asosiy bot bo‘lishi mumkin; alohida bot xohlasangiz, @BotFather'dan yangi bot oching.
3. Kanal ID sini oling (−100… bilan boshlanadi).
4. Serverdagi `.env` ga yozing: `TELEGRAM_REPORT_CHAT_ID=-100…`. Alohida bot bo‘lsa, `TELEGRAM_REPORT_BOT_TOKEN=…` ham yozing. Keyin `docker compose up -d`.
5. Integratsiyalar sahifasida “Telegram hisobot kanali” → **Sinov**.

Kanalga keladi: kunlik hisobot (sozlama `report_time`, odatda 21:00, yoki kun yopilganda — kuniga bir marta), katta kg farqi, kassa farqi, tasdiqlanmagan xarajatlar. Xabar yo‘qolsa ham asl ma’lumot serverda qoladi.

Google Sheets varaqlari (avtomatik yaratiladi): KASSA KIRIM-CHIQIM, TO‘LOVLAR, AVANSLAR, XARAJATLAR, TERIMCHILAR, PAXTA-PUNKT, DEBITOR-KREDITOR, KUNLIK-YOPILISH, NAYMAN TO‘LOVLARI. Har qator ID bilan yangilanadi — qayta yuborilsa ham takror qator bo‘lmaydi.
