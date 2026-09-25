# Vakolatlar jadvali

Jadval koddagi `surxon/security.py` dan avtomatik yaratilgan. Tekshiruv server tomonida, xizmat qatlamida bajariladi: web, telefon va Telegram bot uchun bir xil. Menyuni yashirish himoya hisoblanmaydi — ruxsatsiz so‘rov 403 yoki xato bilan qaytadi.

**Kassir cheklovi:** kassir faqat buxgalter tayyorlagan to‘lovlarni “berildi” qiladi, o‘z kassasining bugungi holatini ko‘radi va xarajat yozadi (buxgalter tasdiqlaydi). Kassa kitobi, hisobotlar, terim, telashka va boshqa sahifalar unga server tomonida yopiq.

**Dala hisobchisi va punkt operatori** buxgalteriya/kassa ma’lumotlarini ko‘rmaydi.

**Punkt operatori cheklovi:** punkt operatori (masalan Yunus) bitta punktga bog‘lanadi. U faqat o‘z punktiga jo‘natilgan telashkalarni ko‘radi va qabul qiladi; boshqa barcha sahifalar (terim, kassa, admin, hisobotlar, qidiruv) server tomonida yopiq — so‘rov punkt ekraniga qaytariladi yoki 403 bo‘ladi.

**Brigadir cheklovi:** brigadir foydalanuvchisi bitta brigadaga bog‘lanadi. U faqat o‘z brigadasiga biriktirilgan dalalarda reys ochadi, terim yozadi, TOLDI qiladi, rasm va hisobotlarni ko‘radi. Boshqa brigadaning reysi va rasmi unga 403 qaytaradi.

| Vakolat | Admin | Rahbar | Brigadir | Tarozi xodimi | Buxgalter | Kassir | Hisobchi (terim) | Haydovchi | Punkt operatori |
|---|---|---|---|---|---|---|---|---|---|
| Bosh sahifa va umumiy ko‘rsatkichlar (`dashboard`) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| Punkt ekrani: yo‘ldagi va kelgan yuklarni ko‘rish (`station.view`) | ✅ | ✅ | — | — | ✅ | — | — | — | ✅ |
| Punktda KELDI, punkt tarozisi va QABUL QILINDI (`station.receive`) | ✅ | ✅ | — | — | — | — | — | — | ✅ |
| Terim (kg) kiritish, o‘z yozuvini bekor qilish (`harvest.write`) | ✅ | ✅ | ✅ | — | — | — | ✅ | — | — |
| Telashka reysini ochish (`load.open`) | ✅ | ✅ | ✅ | — | — | — | ✅ | — | — |
| TOLDI (rasm bilan) qilish (`load.full`) | ✅ | ✅ | ✅ | — | — | — | ✅ | ✅ | — |
| Xato TOLDI ni qayta ochish (sabab bilan) (`load.reopen`) | ✅ | ✅ | — | — | — | — | — | — | — |
| Ishchi qo‘shish/tahrirlash (`workers.write`) | ✅ | ✅ | ✅ | ✅ | — | — | ✅ | — | — |
| Tarozi: brutto va tara kiritish (`weigh.write`) | ✅ | ✅ | — | ✅ | — | — | — | — | — |
| Tortishni tuzatish (sabab, audit) (`weigh.correct`) | ✅ | ✅ | — | — | — | — | — | — | — |
| Nakladnoylarni ko‘rish (`waybill.view`) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — | ✅ | — |
| Nakladnoyni bekor qilish (`waybill.void`) | ✅ | ✅ | — | — | — | — | — | — | — |
| Nayman qabulini kiritish (`nayman.write`) | ✅ | ✅ | — | — | ✅ | — | — | — | — |
| Nayman to‘lovlarini ko‘rish (`payments.view`) | ✅ | ✅ | — | — | ✅ | — | — | — | — |
| Nayman to‘lovini kiritish (`payments.write`) | ✅ | ✅ | — | — | ✅ | — | — | — | — |
| Kassa kitobini ko‘rish (`cash.view`) | ✅ | ✅ | — | — | ✅ | — | — | — | — |
| Kassa kirimi / avans / boshqa kassa yozuvi (`cash.write`) | ✅ | — | — | — | ✅ | — | — | — | — |
| O‘z kassasi smenasi (bugungi kirim/chiqim/qoldiq) (`cash.shift`) | ✅ | ✅ | — | — | ✅ | ✅ | — | — | — |
| Xarajat yozish (`expenses.write`) | ✅ | ✅ | — | — | ✅ | ✅ | — | — | — |
| Xarajatni tasdiqlash / bekor qilish (`expenses.approve`) | ✅ | — | — | — | ✅ | — | — | — | — |
| Ishchi va kombayn pul hisobini ko‘rish (`settlement.view`) | ✅ | ✅ | — | — | ✅ | — | — | — | — |
| Buxgalteriya bo‘limi (`acct.view`) | ✅ | ✅ | — | — | ✅ | — | — | — | — |
| To‘lov buyruqlarini ko‘rish (`payouts.view`) | ✅ | ✅ | — | — | ✅ | ✅ | — | — | — |
| To‘lovni tayyorlash / bekor qilish / qaytarish (`payouts.prepare`) | ✅ | — | — | — | ✅ | — | — | — | — |
| “Pul berildi” (naqd berish) (`payouts.pay`) | ✅ | — | — | — | ✅ | ✅ | — | — | — |
| Kombayn tarifi va ish hajmi (`combine.finance`) | ✅ | — | — | — | ✅ | — | — | — | — |
| Debitor / kreditor (`debts.write`) | ✅ | — | — | — | ✅ | — | — | — | — |
| Kunni yopish (`dayclose`) | ✅ | — | — | — | ✅ | — | — | — | — |
| Ishlab chiqarish hisobotlari (`reports.view`) | ✅ | ✅ | ✅ | ✅ | ✅ | — | ✅ | — | — |
| Moliya hisobotlari (PDF/Excel) (`reports.finance`) | ✅ | ✅ | — | — | ✅ | — | — | — | — |
| Foto arxivni ko‘rish (`photos.view`) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| Rasm yuklash (`photos.upload`) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| Rasmni yashirish (`photos.void`) | ✅ | ✅ | — | — | — | — | — | — | — |
| Reysni bekor qilish (`records.void`) | ✅ | ✅ | — | — | — | — | — | — | — |
| Kuzatuv: odamlardan kelgan rasm/videolarni ko‘rish (`kuzatuv.view`) | ✅ | ✅ | — | — | — | — | — | — | — |
| Kuzatuv: rasm/video so‘rash, eslatish, bekor qilish (`kuzatuv.request`) | ✅ | ✅ | — | — | — | — | — | — | — |
| Kuzatuv: odamlar, ishchi guruh, jadval, faylni yashirish (`kuzatuv.manage`) | ✅ | ✅ | — | — | — | — | — | — | — |
| Dala, texnika, brigada (`masterdata.write`) | ✅ | ✅ | — | — | — | — | — | — | — |
| Audit tarixi (`audit.view`) | ✅ | ✅ | — | — | ✅ | — | — | — | — |
| Loginlar (`users.manage`) | ✅ | — | — | — | — | — | — | — | — |
| Sozlamalar va integratsiyalar (ERP, TV) (`settings.manage`) | ✅ | — | — | — | — | — | — | — | — |
| Mavsumni yopish/ochish (`seasons.manage`) | ✅ | — | — | — | — | — | — | — | — |
| Zaxira nusxalar (`backup.manage`) | ✅ | — | — | — | — | — | — | — | — |

## Rollar bo‘yicha birinchi ekran (telefon)

| Rol | Birinchi ekranda | Asosiy amallar |
|---|---|---|
| Brigadir / hisobchi | Bugun terilgan kg, ochiq telashkalar | Terim kiritish · TOLDI + rasm · Yangi telashka · Rasm |
| Haydovchi | Ochiq telashkalar | TOLDI + rasm · Rasm |
| Tarozi xodimi | Navbatdagi telashkalar, bugungi netto | Tarozi navbati · Bugungi nakladnoylar · Rasm |
| Buxgalter | Qabul kutayotgan yuk, Naymandan qarz, tushum, xarajat | Nayman qabuli · To‘lov · Xarajat · Hisobotlar |
| Punkt operatori | Yo‘lda / Keldi / Bugun qabul, QR skaner, raqam orqali topish | Punkt · Tarix · QR · Parol |
| Kassa (Asadbek) | Kassa qoldig‘i, bugungi kirim/chiqim | Kassa · Ishchilar hisobi · Xarajat · To‘lovlar |
| Rahbar / Admin | Terim, netto, jo‘natilgan, qabul, qarz, kassa, telashkalar | To‘liq dashboard · Telashkalar · Nakladnoylar · Hisobotlar |

Qolgan bo‘limlar “Boshqa” menyusida. Kompyuterda to‘liq dashboard ochiladi.

## Tashqi ulanishlar

- **Azizbek ERP** — alohida kalit, faqat GET, faqat Admin belgilagan ma’lumot turlari (`docs/ERP_API.md`).
- **TV ekran** — alohida kalit. Faqat ishlab chiqarish ko‘rsatkichlari ko‘rinadi, pul va ishchi ismlari ko‘rinmaydi.
