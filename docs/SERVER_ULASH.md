# Serverga o'rnatish va ulanishlar — tartib bilan

Maxfiy ma'lumotlar (tokenlar, parollar, JSON kalit) **faqat server terminalida** kiritiladi. Ular chatga, GitHub'ga va skrinshotga tushmasligi kerak. Kiritish joyi — `tools/sozlash.sh`: u yozilgan matnni ekranda ko'rsatmaydi va faqat serverdagi `.env` fayliga yozadi (bu fayl faqat root uchun ochiq).

| # | Qadam | Buyruq (serverda) | Qanday tekshiriladi |
|---|---|---|---|
| 1 | Serverga kirish | `ssh root@88.198.122.72` | `root@surxan-paxta:~#` chiqadi |
| 2 | O'rnatish + HTTPS | `curl -fsSL …/tools/server_ornatish.sh -o ornatish.sh && bash ornatish.sh` | "TAYYOR: https://…" yozuvi chiqadi, manzil telefonda ochiladi |
| 3 | Narxlar | `bash tools/sozlash.sh narx` | Kombaynlar sahifasida tarif 1 500 000 so'm/t |
| 4 | Telegram bot, arxiv va hisobot kanali | `bash tools/sozlash.sh telegram` | Kanallarga sinov xabari keladi, `holat` → ISHLAYDI |
| 5 | Google Sheets | `bash tools/sozlash.sh sheets` | Jadval tuzilmasi chiqadi (faqat o'qiladi), keyin `SPX …` varaqlari to'ladi |
| 6 | Tashqi zaxira + tiklash | `bash tools/sozlash.sh zaxira` | Zaxira tashqi joyga ko'chadi, qayta yuklab olinib alohida bazaga tiklanadi, sonlar mos keladi |
| 7 | Umumiy holat | `bash tools/sozlash.sh holat` | Har bir bo'lim: ISHLAYDI / ULANMAGAN / TEKSHIRILMAGAN / XATO |

## Sinov va haqiqiy baza alohida
- Haqiqiy baza serverda: `/opt/surxan-paxta.uz/data/surxon.sqlite3`. Namunaviy ma'lumot unga hech qachon yozilmaydi: `demo-data` buyrug'i faqat `APP_MODE=test` rejimida ishlaydi.
- Kompyuterdagi sinov bazasi (`data-test/`) serverga yuklanmaydi.
- Yangi serverda faqat zarur narsalar bor: admin, 3 ta brigada, texnika, "Nayman-1" punkti va "Paxta mavsumi kassasi". Pul, qoldiq va xodimlar yo'q.
- Boshlang'ich qoldiqlarni siz kiritasiz: **Kassa kitobi → Boshlang'ich qoldiq**.

## Narxlar
Hozirgi narx: qo'l terimi **1 500 so'm/kg**, kombayn **1 500 000 so'm/tonna** (1 500 so'm/kg). Har bir tortish o'z vaqtidagi narx bilan saqlanadi. Kunlar bo'yicha narxlarni ko'rish: **Hisobotlar → Narxlar tarixi**. Narx o'zgartirilsa, bu audit jurnalida qoladi (kim, qachon, eski va yangi narx).

## Google Sheets
- Tizim faqat nomi **`SPX `** bilan boshlanadigan o'z varaqlariga yozadi, masalan `SPX KASSA KIRIM-CHIQIM`. Qo'lda yaratilgan varaqlarga va ulardagi formulalarga tegmaydi.
- Agar shu nomdagi varaqda boshqa sarlavhalar bo'lsa, tizim u yerga yozmaydi va buni xato sifatida ko'rsatadi.
- Har bir qator ID bo'yicha yangilanadi (INC-/EXP-/PAY-…), shuning uchun qayta yuborilganda takror qator paydo bo'lmaydi.
- Formulalaringiz `SPX …` varaqlaridagi ma'lumotlarga havola qilishi mumkin.

## Zaxira va tiklash
`flask backup-verify --offsite` quyidagilarni bajaradi:
1. yangi zaxira oladi (baza, CSV va rasmlar);
2. uni tashqi joyga ko'chiradi;
3. tashqi joydan qayta yuklab oladi;
4. **alohida bazaga tiklaydi**;
5. terim kg, kassa qoldig'i, nakladnoy va boshqa sonlarni ishlayotgan baza bilan solishtiradi.

Natija `holat` jadvalida ko'rinadi.
