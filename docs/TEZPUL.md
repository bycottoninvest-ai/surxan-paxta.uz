# SURXON TEZ-PUL — talon bo‘yicha qo‘l terimi puli

Dizayn versiyasi: **v2 (2026-10-11)** — egasining “PAXTA TERIM TALONI” dizayni + bank chekidek ikki qism:
chapda NAZORAT QISMI (bizda qoladi, papka temiriga teshiladi), o‘ngda terimchi qismi (chizg‘ich bilan yirtib beriladi).
Tasdiqlangan dizaynni o‘zgartirish faqat egasining roziligi bilan; o‘zgarsa — versiya raqami oshiriladi.

## Ish tartibi
1. **Talon (Admin/Rahbar):** Buxgalteriya → TEZ-PUL → Talonlar → “Talonlar yaratish” → PDF: A4 ga 2 ta talon.
   Ikkala qismda bir xil raqam va QR; muhr chiziq ustiga bosiladi. Talon **sanasiz, bir marta** chop etiladi —
   ishlatilmaganlari keyingi kunlarga qoladi; sanani tizim skaner paytida o‘zi yozadi.
2. **Daftar:** “talon № — ism”. Ism telefonga kiritilmaydi.
3. **Terim hisobchisi (2 qadam):** telashka ochilsa — darhol talon skaneri: QR → kg → **SAQLASH** → kamera o‘zi keyingi talonga.
   Kg asosiy **Qo‘l terimi**ga shu telashkaning oddiy tortishi bo‘lib yoziladi (telashka narxi bilan).
   50–100 kishilik guruh — **“👥 Starshi (guruh)”** (bitta talon, 20 000 kg gacha); oddiy talon — 250 kg gacha.
   Internet bo‘lmasa — telefonda navbatga turadi va keyin o‘zi yuboriladi (bir marta yoziladi).
4. **Kassir (2 qadam):** QR → summa o‘zi chiqadi → **PUL BERILDI**. Pul mavjud kassadan oddiy “Ishchiga to‘lov” (PAY-…) bo‘lib chiqadi.
5. **Rahbar:** Buxgalteriya → TEZ-PUL panel: bugun yozilgan kg / hisoblangan summa, berilgan pul, to‘lanmagan, brigadalar
   (punkt qabuli bilan farq), kassirlar va kassa qoldig‘i, hisobchilar, rad etilgan skanerlar; Excel / chop.

## Himoya
- Bir talon — bir tortish (dalada ikkinchi marta yozilmaydi), bir to‘lov (bitta UPDATE … WHERE status, IMMEDIATE tranzaksiya):
  nusxa, qayta skaner, ikki kassir bir paytda — bitta to‘lov.
- QR = `SPX-T:<raqam>:<tasodifiy kod>`; soxta QR qabul qilinmaydi. Qo‘lda: raqam + talondagi “Kod”.
- Kassir kg yozmaydi va o‘zgartira olmaydi; kg yozilmagan talonga pul berilmaydi; internet yo‘q — PUL BERILDI yopiq.
- Kg xato yozilgan bo‘lsa — tortish odatdagidek bekor qilinadi, talon qayta skanerlanadi. Hech narsa o‘chirilmaydi, hammasi auditda.
- Har talon “Talon 000274” kartasi (ism ro‘yxatlarida ko‘rinmaydi); to‘lov Telegram guruhiga alohida yuborilmaydi.
