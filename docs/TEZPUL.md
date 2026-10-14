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
   Har odamning taloni alohida yoziladi (bitta talonga 250 kg gacha).
   Internet bo‘lmasa — telefonda navbatga turadi va keyin o‘zi yuboriladi (bir marta yoziladi).
4. **Kassir (2 qadam), ikki rejim:**
   - bitta odam: QR → summa o‘zi chiqadi → **PUL BERILDI**;
   - starshi o‘z guruhining 50–60 talonini olib keladi: QR → “📷 + Yana talon” → QR … → jami summa → bitta **PUL BERILDI (N)**.
   Hammasi bitta tranzaksiyada: yo hamma talon to‘lanadi, yo hech biri. Pul mavjud kassadan oddiy “Ishchiga to‘lov” (PAY-…) bo‘lib chiqadi.
5. **Rahbar:** Buxgalteriya → TEZ-PUL panel: bugun yozilgan kg / hisoblangan summa, berilgan pul, to‘lanmagan, brigadalar
   (punkt qabuli bilan farq), kassirlar va kassa qoldig‘i, hisobchilar, rad etilgan skanerlar; Excel / chop.

## Himoya
- Bir talon — bir tortish (dalada ikkinchi marta yozilmaydi), bir to‘lov (bitta UPDATE … WHERE status, IMMEDIATE tranzaksiya):
  nusxa, qayta skaner, ikki kassir bir paytda — bitta to‘lov.
- QR = `SPX-T:<raqam>:<tasodifiy kod>`; soxta QR qabul qilinmaydi. Qo‘lda: raqam + talondagi “Kod”.
- Kassir kg yozmaydi va o‘zgartira olmaydi; kg yozilmagan talonga pul berilmaydi; internet yo‘q — PUL BERILDI yopiq.
- Kg xato yozilgan bo‘lsa — tortish odatdagidek bekor qilinadi, talon qayta skanerlanadi. Hech narsa o‘chirilmaydi, hammasi auditda.
- Har talon “Talon 000274” kartasi (ism ro‘yxatlarida ko‘rinmaydi); to‘lov Telegram guruhiga alohida yuborilmaydi.

## v3 (2026-10-13) — STARSHI TALONI va kechki hisob
- Talon **faqat starshiga** beriladi (har odamga alohida kichik talon chiqarilmaydi). Chop etish: Talonlar → 👥 Starshi taloni,
  bitta talon 2–5 A4 varaq (100 kishi uchun 5 varaq): har qatorda ism familiya, **Abetgacha / Abetdan keyin / JAMI** kg;
  1-varaqda Starshi, Dala/brigada, Sana, **Telashka №**, Odam soni, **JAMI KG**; har varaqda raqam + QR va “N-varaq jami”.
- **Kun bo‘yi:** qo‘l terimi telashkasi to‘lishi bilan yopiladi — hisobchi yopishda **telashkaning kg** ini yozadi (blankadagi raqam).
  Ochiq telashka sahifasida bitta katta tugma: **🚜 Telashkani yopish — kg yozish** (kunduzi talon skanerlanmaydi;
  talon skaneri va eski usul pastda kichik havola bo‘lib qoladi).
- **Kechqurun, kassa oldida (hisobchi yoki kassir):** 🌙 Kechki hisob → brigada → starshi talonini skanerlash → “Bugun nechta odam?”
  → tizim nechta varaq rasmga olishni aytadi (30 odam → 2 varaq) → har varaqni rasmga olish (qora parda ismlarni yopadi)
  → tizim kg ni o‘qib qo‘shadi, mos kelmagan qatorni rangli qiladi → hisobchi tekshiradi → “✅ Tugadi — jami N kg” → SAQLASH.
  Kg o‘sha kungi brigadaning ish haqiga (oxirgi telashkasiga) yoziladi, telashkaning o‘z kg i o‘zgarmaydi. Keyin kassir PUL BERILDI.
- **Nazorat:** panelda har brigada uchun 3 ta raqam — talon kg (pul) · telashkalar (dala) · punkt; 3% dan ko‘p farq qizil.
  O‘zi kg yozib o‘zi pul bergan — qizil. Saqlangan starshi taloni **PDF** bo‘lib direktor va buxgalterga (Telegram) boradi va panelda turadi.
- **AI o‘qish** (Claude, pullik): kalit server `.env` da (`ANTHROPIC_API_KEY`) yoki admin uni **Admin → Integratsiyalar** ga qo‘yadi —
  tekshiriladi va baza yonidagi yopiq faylga yoziladi (bazaga, zaxiraga, auditga tushmaydi; faqat oxirgi 4 belgisi ko‘rinadi); `.env` ustun.
  Ismlar yuborilmaydi, faqat kg ustunlari.
  Kalitsiz ham ishlaydi: rasm dalil bo‘lib saqlanadi, varaq jamisi qo‘lda yoziladi. Model: Admin → Integratsiyalar (Opus 5.5 / Haiku 5.5).

## v3.1 (2026-10-14) — kassada to‘liq varaq, PUL BERILDI PDF, shofyorlar
- **Kassir / buxgalter** starshi talonini skanerlaganda: tepada **JAMI BERILADI**, tagida har qator (Abetgacha · Keyin · JAMI kg · Summa)
  va varaq rasmlari (ismlar bilan — kassir shu talonning rasmlarini ko‘ra oladi).
- **PUL BERILDI** bosilganda direktor va buxgalterga PDF: yashil “PUL BERILDI: N so‘m · kassir · vaqt”, har qatorning kg va summasi.
- **Kechki hisob:** xato qator ostida aniq izoh (“47 + 11 = 58, lekin JAMI ga 64 yozilgan — farq 6 kg”) va bir bosishda tuzatish;
  ismli varaq rasmi faqat telefonda ko‘rinadi (AI ga yuborilmaydi).
- **Shofyorlar (odam tashish):** TEZ-PUL → 🚐 Shofyorlar — ro‘yxat va narx (sozlama `driver_rate_person`, standart 10 000 so‘m/odam).
  Kechki hisobda “Odamlarni qaysi shofyor olib keldi?” (yoki “Shofyorsiz”). Shofyorga **talonda kg i yozilgan odamlar soni** bo‘yicha
  hisoblanadi — starshi yoki shofyor aytgan son bo‘yicha emas (panelda “starshi: N” qizil bo‘lib farq ko‘rinadi).
  Kassir: TEZ-PUL kassa → 🚐 Shofyorlar → BERILDI — bitta kassa chiqimi (“Shofyor (odam tashish)”), direktor va buxgalterga xabar.
  Narx o‘zgarsa — avval hisoblangan talonlar o‘z narxida qoladi.

## v3.2 (2026-10-14) — telashka dalasi GPS bo‘yicha
- Telashka ochilganda dala **faqat telefon turgan dala** bo‘lsa o‘zi tanlanadi (chegarada GPS xatosi hisobga olinadi).
  Telefon hech qaysi dala ichida bo‘lmasa — “— dala tanlanmagan —”, ro‘yxatdagi birinchi dala endi o‘zi qolmaydi.
- Telefon bir dalada, ro‘yxatda boshqasi tanlansa — ochishdan oldin so‘raladi; ochilsa **Nazorat**da qizil:
  “TL-02 boshqa dalaga ochilgan”. Rahbar: telashka → Hujjatlar → “dala / brigada noto‘g‘rimi? Tuzatish” — telashka,
  tortishlar va starshi talonlari to‘g‘ri dala va brigadaga o‘tadi (kg va pul o‘zgarmaydi, auditda qoladi).
