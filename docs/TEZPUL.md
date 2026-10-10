# SURXON TEZ-PUL — talon bo‘yicha qo‘l terimi puli

Dizayn versiyasi: **v1 (2026-10-10)** — egasi yuborgan “PAXTA TERIM TALONI” va “Daftar + Talon + Kg” rasmlari asosida.
Tasdiqlangan dizaynni o‘zgartirish faqat egasining roziligi bilan; o‘zgarsa — versiya raqami oshiriladi.

## Ish tartibi
1. **Fabrika (Admin/Rahbar):** Buxgalteriya → TEZ-PUL → Talonlar → “Talonlar yaratish” → PDF (A4 ga 2 ta, har biri o‘z raqami).
   Pachkani brigadaga berish: “dan … gacha → brigada”.
2. **Daftar:** mas’ul xodim “talon № — ism” yozadi. Ism tizimga kiritilmaydi.
3. **Dala (telefonsiz):** tarozichi faqat kg ni talonga yozadi.
4. **Kassa (kassir telefoni, 2 qadam):** QR skaner → talondagi kg → summa o‘zi chiqadi → **PUL BERILDI**.
   Pul mavjud kassadan “Qo‘l terim (talon)” chiqimi (PAY-…) bo‘ladi.
5. **Rahbar:** Buxgalteriya → TEZ-PUL panel: bugungi kg, berilgan pul, tasdiq kutayotganlar, brigadalar (punkt qabuli bilan farq),
   kassirlar va kassa qoldig‘i, rad etilgan skanerlar; Excel / chop.

## Himoya
- Talon bir marta to‘lanadi (bitta UPDATE … WHERE status, IMMEDIATE tranzaksiya): nusxa, qayta skaner, ikki kassir bir paytda — bitta to‘lov.
- QR = `SPX-T:<raqam>:<tasodifiy kod>`; kod bazada saqlanadi, soxta QR qabul qilinmaydi. Qo‘lda: raqam + talondagi “Kod”.
- Internet yo‘q — PUL BERILDI yopiq; oflayn navbat yo‘q. Bir bosish ikki marta yetib borsa — `pay_uuid` bilan bitta to‘lov.
- Bitta talonga `tezpul_max_kg` (150) dan ko‘p — rahbar tasdig‘i; tasdiqdan keyin kg qotadi, kassir o‘zgartira olmaydi.
- Bekor qilingan (yo‘qolgan) talon — qizil “BEKOR”. Barcha amallar audit jurnalida, hech narsa o‘chirilmaydi.
- Har bir talon to‘lovi Telegram hisobot guruhiga alohida yuborilmaydi — direktor hisobotida bitta qator.

## Sozlamalar
`tezpul_rate_kg` (2000 so‘m/kg, to‘lov paytidagi narx talonda qotadi), `tezpul_max_kg` (150).

## Hali qilinmagan
- To‘langan talon summasini tuzatish (qaytarish / qo‘shimcha) — hozircha kassa “Tuzatish” tartibi orqali.
- Talon kg i “Qo‘l terimi” (ishchilar) ro‘yxatiga qo‘shilmaydi — alohida TEZ-PUL hisobida; bir paxtani ikki marta yozmaslik uchun
  talon bilan pul oladigan terimchini hisobchi dalada telefonga kiritmaydi.
