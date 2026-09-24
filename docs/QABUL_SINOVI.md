# Serverdagi qabul sinovi: haqiqiy telefon va Telegram bilan bitta reys

**Holat: HALI BAJARILMAGAN.** Server va Telegram bot hali yo‘q. Bu sinov server ishga tushgach (vaqtinchalik HTTPS yoki domen bilan) o‘tkaziladi. Natijalar pastdagi jadvalga yoziladi.

## Tayyorgarlik

- [ ] Server ishlayapti: `flask smoke-check` — Baza OK, Telegram webhook OK.
- [ ] Haqiqiy dala kiritilgan (kontur va gektar) va brigadirga biriktirilgan.
- [ ] Loginlar: brigadir (Telegram ulangan), tarozi xodimi (Telegram ulangan), buxgalter, kassa, rahbar.
- [ ] Sinov uchun narx kiritilgan **yoki** “narx yo‘q” holati ataylab tekshiriladi.
- [ ] Brigadir telefoni: oddiy Android, mobil internet (Wi-Fi emas).

## Qadamlar

| # | Kim / qayerda | Amal | Kutilgan natija | Natija | Izoh |
|---|---|---|---|---|---|
| 1 | Brigadir · telefon brauzeri | Kirish → bosh sahifa | Rolga mos ekran, 4 ta katta tugma | | |
| 2 | Brigadir · telefon | Yangi telashka: TL-?, dala, traktor | Reys ochildi | | |
| 3 | Brigadir · telefon | 3 ta ishchi: ro‘yxatdan tanlash + yangi ism, kg | Har biri “Saqlandi”, jami kg to‘g‘ri | | |
| 4 | Brigadir · telefon | Internetni o‘chirib 1 ta terim kiritish → internetni yoqish | “Internet yo‘q · navbatda 1” → keyin “yuborildi”, **bitta** yozuv | | |
| 5 | Brigadir · Telegram | “Terim kiritish” → `Ism 85` qatorlari | Botda ✅ qatorlar, web'da ham ko‘rinadi | | |
| 6 | Brigadir · telefon kamerasi | TOLDI: 2 ta rasm → TOLDI | TOLDI, ichki kg qotirildi, rasmlar arxivda | | |
| 7 | Rahbar · Telegram | — | “TL-? TOLDI” xabari keldi | | |
| 8 | Tarozi · Telegram yoki web | Brutto + tablo rasmi | Brutto va vaqt saqlandi | | |
| 9 | Tarozi | Tara → “Tortishni yakunlash” | Netto, PA-00000N, **PDF avtomatik** | | |
| 10 | Buxgalter · kompyuter | Nakladnoy → PDF (Nayman nusxasi) | Narx va summa **yo‘q**, o‘zbekcha harflar to‘g‘ri | | |
| 11 | Arxiv kanali (agar ulangan) | — | 2 ta PDF + reys rasmlari keldi | | |
| 12 | Buxgalter | Nayman qabuli: kg, sabab | Farq to‘g‘ri, holat “Qabul qilindi” | | |
| 13 | Buxgalter | To‘lov kiritish (nakladnoyga) | Qarz kamaydi (narx bo‘lsa) yoki “hisoblanmagan” | | |
| 14 | Kassa | Ishchiga avans (boshlang‘ich qoldiq kiritilgan bo‘lsa) | Qoldiq kamaydi | | |
| 15 | Rahbar · kompyuter | Dashboard, Audit, Foto arxiv | Butun zanjir, kim/qachon, rasmlar | | |
| 16 | Rahbar | Hisobot → Kunlik → Excel va PDF | Fayllar ochiladi, raqamlar mos | | |
| 17 | Google Sheets (agar ulangan) | — | Nakladnoy va to‘lov qatorlari | | |
| 18 | TV (agar ulangan) | `/tv` | Raqamlar yangilandi, pul va ismlar yo‘q | | |

Sinov o‘tkazgan: ______________  Sana: ______________  Server manzili: ______________
