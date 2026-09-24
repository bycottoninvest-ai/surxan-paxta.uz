# Vakolatlar jadvali

Jadval koddagi `surxon/security.py` dan avtomatik yaratilgan. Tekshiruv server tomonida, xizmat qatlamida bajariladi: web, telefon va Telegram bot uchun bir xil. Menyuni yashirish himoya hisoblanmaydi — ruxsatsiz so‘rov 403 yoki xato bilan qaytadi.

**Brigadir cheklovi:** brigadir foydalanuvchisi bitta brigadaga bog‘lanadi. U faqat o‘z brigadasiga biriktirilgan dalalarda reys ochadi, terim yozadi, TOLDI qiladi, rasm va hisobotlarni ko‘radi. Boshqa brigadaning reysi va rasmi unga 403 qaytaradi.

| Vakolat | Admin | Rahbar | Brigadir | Tarozi xodimi | Buxgalter | Kassa | Hisobchi (terim) | Haydovchi |
|---|---|---|---|---|---|---|---|---|
| Bosh sahifa va umumiy ko‘rsatkichlar (`dashboard`) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Terim (kg) kiritish, o‘z yozuvini bekor qilish (`harvest.write`) | ✅ | ✅ | ✅ | — | — | — | ✅ | — |
| Telashka reysini ochish (`load.open`) | ✅ | ✅ | ✅ | — | — | — | ✅ | — |
| TOLDI (rasm bilan) qilish (`load.full`) | ✅ | ✅ | ✅ | — | — | — | ✅ | ✅ |
| Xato TOLDI ni qayta ochish (sabab bilan) (`load.reopen`) | ✅ | ✅ | — | — | — | — | — | — |
| Ishchi qo‘shish/tahrirlash (`workers.write`) | ✅ | ✅ | ✅ | ✅ | — | — | ✅ | — |
| Tarozi: brutto va tara kiritish (`weigh.write`) | ✅ | ✅ | — | ✅ | — | — | — | — |
| Tortishni tuzatish (sabab, audit) (`weigh.correct`) | ✅ | ✅ | — | — | — | — | — | — |
| Nakladnoylarni ko‘rish (`waybill.view`) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — | ✅ |
| Nakladnoyni bekor qilish (`waybill.void`) | ✅ | ✅ | — | — | — | — | — | — |
| Nayman qabulini kiritish (`nayman.write`) | ✅ | ✅ | — | — | ✅ | — | — | — |
| To‘lovlarni ko‘rish (`payments.view`) | ✅ | ✅ | — | — | ✅ | ✅ | — | — |
| To‘lov kiritish/bekor qilish (`payments.write`) | ✅ | ✅ | — | — | ✅ | ✅ | — | — |
| Kassani ko‘rish (`cash.view`) | ✅ | ✅ | — | — | ✅ | ✅ | — | — |
| Kassa kirim/chiqim (`cash.write`) | ✅ | — | — | — | ✅ | ✅ | — | — |
| Xarajat kiritish (`expenses.write`) | ✅ | ✅ | — | — | ✅ | ✅ | — | — |
| Ishchilar hisob-kitobi (`settlement.view`) | ✅ | ✅ | — | — | ✅ | ✅ | ✅ | — |
| Ishlab chiqarish hisobotlari (`reports.view`) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| Moliyaviy hisobotlar (`reports.finance`) | ✅ | ✅ | — | — | ✅ | ✅ | — | — |
| Foto arxivni ko‘rish (`photos.view`) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Rasm yuklash (`photos.upload`) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Rasmni yashirish (`photos.void`) | ✅ | ✅ | — | — | — | — | — | — |
| Reysni bekor qilish (`records.void`) | ✅ | ✅ | — | — | — | — | — | — |
| Dala, texnika, brigada (`masterdata.write`) | ✅ | ✅ | — | — | — | — | — | — |
| Audit tarixi (`audit.view`) | ✅ | ✅ | — | — | ✅ | — | — | — |
| Loginlar (`users.manage`) | ✅ | — | — | — | — | — | — | — |
| Sozlamalar va integratsiyalar (ERP, TV) (`settings.manage`) | ✅ | — | — | — | — | — | — | — |
| Mavsumni yopish/ochish (`seasons.manage`) | ✅ | — | — | — | — | — | — | — |
| Zaxira nusxalar (`backup.manage`) | ✅ | — | — | — | — | — | — | — |

## Rollar bo‘yicha birinchi ekran (telefon)

| Rol | Birinchi ekranda | Asosiy amallar |
|---|---|---|
| Brigadir / hisobchi | Bugun terilgan kg, ochiq telashkalar | Terim kiritish · TOLDI + rasm · Yangi telashka · Rasm |
| Haydovchi | Ochiq telashkalar | TOLDI + rasm · Rasm |
| Tarozi xodimi | Navbatdagi telashkalar, bugungi netto | Tarozi navbati · Bugungi nakladnoylar · Rasm |
| Buxgalter | Qabul kutayotgan yuk, Naymandan qarz, tushum, xarajat | Nayman qabuli · To‘lov · Xarajat · Hisobotlar |
| Kassa (Asadbek) | Kassa qoldig‘i, bugungi kirim/chiqim | Kassa · Ishchilar hisobi · Xarajat · To‘lovlar |
| Rahbar / Admin | Terim, netto, jo‘natilgan, qabul, qarz, kassa, telashkalar | To‘liq dashboard · Telashkalar · Nakladnoylar · Hisobotlar |

Qolgan bo‘limlar “Boshqa” menyusida. Kompyuterda to‘liq dashboard ochiladi.

## Tashqi ulanishlar

- **Azizbek ERP** — alohida kalit, faqat GET, faqat Admin belgilagan ma’lumot turlari (`docs/ERP_API.md`).
- **TV ekran** — alohida kalit. Faqat ishlab chiqarish ko‘rsatkichlari ko‘rinadi, pul va ishchi ismlari ko‘rinmaydi.
