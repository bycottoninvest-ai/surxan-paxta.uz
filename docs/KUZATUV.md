# Kuzatuv — tizim odamlardan rasm/video so‘raydi

**Maqsad:** rahbar har kuni dalada nima bo‘layotganini odamlar orqali ko‘rib turadi. Tizim Telegram orqali
“📸 Mirjalol, traktor ishlayotgan rasm yoki videoni yuboring” deb so‘raydi. Odam javob yuborgan rasm yoki video
serverga saqlanadi va rahbarning **Kuzatuv** sahifasida hamda bosh sahifasida ko‘rinadi.

## Ikki bot / kanal

| | Nima qiladi | Kim yozadi |
|---|---|---|
| **1. Hisobot kanali** (`TELEGRAM_REPORT_CHAT_ID`) | Har bir muhim hodisa darhol + kunlik hisobot. Hammasi kanalda saqlanib qoladi | Faqat tizim. Odamlar yozolmaydi (kanal) |
| **2. Ish boti** (`TELEGRAM_BOT_TOKEN`) | Odamlardan rasm/video so‘raydi, javobni qabul qiladi. Hisobchilar terim ham yozadi | Tizim so‘raydi, odam javob beradi |

Kuzatuv rasmlari va videolari yopiq **arxiv kanali**ga ham nusxa bo‘lib tushadi (`TELEGRAM_ARCHIVE_CHAT_ID`).

## Odam ro‘yxatga qanday tushadi

1. **Havola bilan:** Kuzatuv → Odamlar → “Yangi odam qo‘shish” (ism, vazifasi: Traktorchi / Brigadir / Agronom…). Tizim
   havola beradi (`https://t.me/<bot>?start=K-XXXXXX`). Uni odamga Telegramda yuborasiz, u bosib **Start** qiladi — tamom.
2. **Ishchi guruh orqali (avtomatik):** botni ishchilar Telegram guruhiga qo‘shing va **admin** qiling. Keyin Kuzatuv →
   Odamlar → “Telegram guruhlar” da **Ishchi guruh qilish** ni bosing. Guruhga qo‘shilgan har bir odam avtomatik ro‘yxatga
   tushadi (faol). Guruhdan chiqsa — o‘chiriladi. Bot admin bo‘lmasa, guruhdagi eski a’zolarni ular biror narsa yozgandagina ko‘radi.
3. **Tizim foydalanuvchilari** (hisobchi, punkt, kassir…) Telegram akkauntini ulaganda o‘zi qo‘shiladi.

Botga o‘zi yozgan notanish odam “Tasdiq kutmoqda” bo‘lib turadi — rahbar tasdiqlamaguncha undan hech narsa
so‘ralmaydi va uning rasmlari saqlanmaydi.

## So‘rov qanday boradi

- Kuzatuv → **Rasm / video so‘rash**: odam(lar)ni belgilaysiz, tayyor matnni bosasiz (yoki o‘zingiz yozasiz), muddat → **YUBORISH**.
- Odam botni ochgan bo‘lsa — **shaxsiy xabar**; aks holda — **ishchi guruhda** ismini belgilab (mention).
- Odam hali ulanmagan bo‘lsa, so‘rov saqlanadi va sababi ko‘rsatiladi; havolani bosishi bilan o‘zi yetib boradi.
- **Jadval:** Kuzatuv → Jadval: masalan “har kuni 09:00 va 16:00 da traktorchilardan traktor rasmi”. Tizim o‘zi so‘raydi
  (server o‘chib qolsa, 1,5 soatgacha kechikkan vaqt ham so‘raladi, eski kunlar emas). Har vaqt uchun bir marta.
- Muddat o‘tsa — **KECHIKDI**, hisobot kanaliga bir qator yoziladi. Kechikkan javob ham qabul qilinadi.
- “Eslatish” tugmasi so‘rovni qayta yuboradi, “Bekor” yopadi.

## Javob qanday qabul qilinadi

- Odam rasm yoki videoni botga yuboradi (yaxshisi so‘rov xabariga **reply** qilib). Reply bo‘lmasa, uning eng eski ochiq
  so‘roviga bog‘lanadi; ochiq so‘rov bo‘lmasa — “o‘zi yubordi” bo‘lib saqlanadi.
- Guruhda esa faqat so‘rovga javob bo‘lgan rasm/video saqlanadi (guruhdagi boshqa rasmlar olinmaydi).
- Rasm: tekshiriladi, qayta siqiladi, kichik nusxa (thumbnail) yasaladi. Video: 20 MB gacha serverga yuklanadi va sahifada
  telefonda ham o‘ynaydi. **20 MB dan katta video** Telegram cheklovi sababli serverga yuklanmaydi — asli arxiv kanaliga
  ko‘chiriladi, sahifada eslatma chiqadi.
- Bir fayl ikki marta yuborilsa ham bitta yozuv bo‘ladi.
- Hisobchi TOLDI yoki tarozi rasmi yuborayotgan bo‘lsa, rasm o‘sha joyga ketadi (kuzatuvga emas).

## Kim ko‘radi

Rahbar va admin (`kuzatuv.view/request/manage`). Fayllar ochiq internetda emas: faqat tizimga kirganlarga, huquqi borlarga beriladi.
Yashirish — o‘chirish emas: sabab bilan yashiriladi, audit saqlanadi.

## Sozlamalar

- `kuzatuv_deadline_min` — javob muddati (standart 120 daqiqa);
- `kuzatuv_late_alert` — kechiksa hisobot kanaliga yozish (1/0);
- `report_feed` — hisobot kanaliga har bir hodisani yozish (1/0).

## Serverda ulash

`bash tools/sozlash.sh telegram` — token (yashirin kiritiladi), bot username, arxiv va hisobot kanali ID lari.
Webhook `message, callback_query, my_chat_member, chat_member` yangilanishlarini oladi (`flask set-webhook`).
Bot ulanmaguncha so‘rovlar saqlanadi va holati “yuborilmadi: Telegram bot ulanmagan” deb ko‘rinadi.

## Holat (halol)

- Kod, sahifalar, jadval, kechikish, hisobot kanali qatorlari — **tayyor, 8 ta avtomatik test** (soxta Telegram bilan).
- Telefon ko‘rinishi Playwright’da tekshirildi.
- **Haqiqiy Telegram bilan hali sinalmagan** — bot tokeni serverga kiritilgach, bitta odam bilan sinab ko‘rish kerak.
