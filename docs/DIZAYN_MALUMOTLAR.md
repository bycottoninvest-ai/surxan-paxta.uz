# SURXAN-PAXTA.UZ — dizayn uchun: tizimdagi barcha ma'lumotlar (v2.18)

Bu hujjat dizayner (Codex yoki boshqa) uchun. Maqsad: **Bosh dashboard** (kompyuter + planshet), **Direktor paneli**
(telefon + planshet) va **TV ekrani** uchun chiroyli, tez, tushunarli dizayn. Quyida tizimda **haqiqatan bor** har bir
raqam va ro'yxat yozilgan — dizayn faqat shulardan foydalansin, yangi (yo'q) raqam o'ylab topilmasin.

---

## 1. Qat'iy qoidalar (buzilmasin)

1. **Til:** o'zbek lotin. Raqamlar: `3 142 kg`, `1 113 000 so'm` (mingliklar bo'sh joy bilan), sana `27.09.2026`.
2. **Logotip va nom:** SURXON logosi (qizil gul belgisi) va "SURXON TAXIATOSH TEXTILE · PAXTA HISOB TIZIMI" saqlansin.
   Asosiy ranglar: to'q ko'k `#0b2a55`, ko'k `#1b6fe0`, yashil `#1a8a3f`, fon `#eef3f9`. Paxta surati (hero) qoladi.
3. **Bekor qilingan** reys/nakladnoy/tortish **hech qayerda ko'rsatilmaydi va hisoblanmaydi** (faqat Admin arxivi).
4. **Direktor paneli faqat ko'rish uchun** — unda pul yoki kg ni o'zgartiradigan tugma bo'lmaydi.
5. **TV ekranida pul yo'q** (kassa, ish haqi, qarz, narx ko'rsatilmaydi). Ishchi ismi qisqa: "Abdurahmon Q.".
6. So'zlar: "o'g'irlik", "kamomad" ishlatilmaydi. O'rniga: "farq", "me'yordan ko'p", "e'tibor uchun".
7. Ma'lumot yo'q bo'lsa `—` (chiziqcha) yoziladi, uydirma `0` emas. Masalan solyarka ulanmagan bo'lsa "Ulanmagan".
8. Sun'iy yo'ldosh surati **jonli emas** (Google/Esri surati eski) — xaritada texnika/odam belgilari jonli.
9. Tez ishlashi shart: dala internetida (sekin 3G) ham ochilsin. Og'ir kutubxonalar, katta rasmlar yo'q.

## 2. Qurilmalar

| Qurilma | Kim | Nima ochiladi |
|---|---|---|
| Kompyuter (1440–1920 px) | Admin, Rahbar, Buxgalter | To'liq dashboard, barcha bo'limlar |
| **Planshet** (tik 712–834 px, yotiq 1138–1280 px) | Direktor (o'zi), terim hisobchilari | Direktor: to'liq dashboard + Direktor paneli. Hisobchi: "Dala ishi" |
| Telefon (360–430 px) | Hamma | Direktor paneli, hisobchi, punkt, yoqilg'i, kassir |
| TV (1920×1080, uzoqdan ko'riladi) | Ofis | `/tv` — katta raqamlar, xarita, pulsiz |

Planshet: Android planshet va iPad **to'liq dashboard** oladi (telefon esa Direktor paneliga o'tadi).

## 3. Texnik tomoni (dizayn shu tizimga qo'yiladi)

- Flask + Jinja shablonlar (`templates/*.html`), bitta CSS: `static/css/app.css`, JS: `static/js/*.js`.
  Build yo'q (React/Tailwind yo'q). Grafik — oddiy SVG/Canvas (hozir o'zimizniki), xarita — Leaflet.
- Dizayn **HTML + CSS** ko'rinishida berilsa, biz uni shablonga ulaymiz. Sinf nomlari: dashboard `kpis`, `card`,
  direktor paneli `rb-*`, dala `d-*`, TV `tv-*`.
- Har bir blok uchun: bo'sh holat (ma'lumot yo'q), yuklanmoqda, va ko'p ma'lumot (masalan 57 dala) holatini ham chizing.

---

## 4. BOSH DASHBOARD (kompyuter / planshet) — `/`

Tepada davr: **Bugun · Kecha · Mavsum (barchasi)** (yoki istalgan sana).

### 4.1 Asosiy raqamlar (KPI kartalar)
| Nomi | Ma'nosi | Qo'shimcha |
|---|---|---|
| Terim (kg) | Dalada tortilgan jami kg (qo'l + kombayn) | kechagiga nisbatan % (faqat "Bugun"da) |
| — qo'l terimi kg / kombayn kg | ajratilgan | terimchilar soni |
| Umumiy tarozi (netto) | Tarozida tortilgan netto | % trend |
| Naymanga ketgan | Nakladnoy bilan jo'natilgan kg | % trend |
| Qabul qilingan | Punkt qabul qilgan kg | % trend |
| Farq (kg, %) | Punkt qabul − jo'natilgan (faqat qabul qilinganlar) | yo'ldagi reyslar soni |
| Telashkalar (band) | band / jami, "TOLDI → tarozi" soni | |
| Yo'lda | reyslar soni va kg | |

### 4.2 Dalalar xaritasi (4 rejim)
- **Hosildorlik:** har dala konturi rangda (yuqori / o'rtacha / past / ma'lumot yo'q), kg/ga.
- **Brigadirlar:** dala qaysi brigadirniki.
- **Terim holati:** hozir terilayotgan dalalar.
- **Terilgan joylar:** bugun terilgan kataklar (1-, 2-, 3-terim ranglari) + tagida bugungi reyslar rasmlari lentasi.
- **Yo'ldagi traktorlar:** telashkali traktor punktga qarab virtual harakatlanadi, punktga **yetib kelish vaqti** (ETA).
- **Punkt(lar)** belgisi. Nomlar faqat yaqinlashtirganda (zoom ≥ 15).
- Ma'lumot: `fields_json` [{id, code, name, area, brigadier, net, active, poly}], `transit` [{trip, kg, eta, km, progress}],
  `stations` [{name, lat, lon}], `picked` kataklar [{poly, round, today, trip}].

### 4.3 Boshqa bloklar
- **Brigadirlar natijasi:** har brigadir: maydon (ga), netto kg, kg/ga, progress chizig'i.
- **Texnikalar (jonli holat):** har telashka: rasm, kod, kg, holat (Bo'sh / Terim / TOLDI / Yo'lda), dala.
  Traktorlar: kod, operator, dala. Kombaynlar: kod, bugungi kg, dala, holat.
- **Terim grafigi:** Kunlik / Haftalik / Mavsum / Bugun (soatbay): qo'l terimi, kombayn, tarozi netto.
- **Hosildorlik (kg/ga)** dalalar ro'yxati (rasm, maydon, kg/ga chizig'i).
- **Paxta turi:** qo'l terimi va kombayn ulushi (donut).
- **Terim holati / Kombayn holati / Nayman holati** kichik kartalar.
- **Bugungi terimchilar (top 10):** F.I.Sh., brigadir, dala, kg.
- **Nakladnoylar (bugun):** №, vaqt, telashka, traktor, brigadir, netto, holat.
- **So'nggi voqealar:** "TL-02 to'ldi", "PA-000003 nakladnoy yaratildi", "Punkt qabul qildi"… (vaqt bilan).
- **Kuzatuv (bugun):** so'raldi / javob / kutilmoqda (rasm-video so'rovlari).
- **Foto arxiv:** oxirgi 6 rasm.
- **Nayman qabuli (bugun):** vaqt, nakladnoy, qabul kg, farq, izoh, holat.
- **Moliya (faqat admin/rahbar/buxgalter):** mavsum netto, jo'natilgan, qabul, kassa qoldig'i.
- **Mavsumlar solishtiruvi:** yil, maydon, kg, kg/ga.

## 5. DIREKTOR PANELI (telefon / planshet) — `/rahbar`
Davr: **Bugun · Hafta · Mavsum**. Qidiruv (texnika, reys, nakladnoy, dala, ishchi). 30 soniyada yangilanadi.

6 ta rangli karta:
1. **PAXTA** — terilgan kg (dala hisobi); jo'natilgan netto; punkt qabul; farq.
2. **YO'LDA** — reyslar soni va kg; dalada to'lgan; punktda navbatda; terilmoqda.
3. **SOLYARKA** — olindi / berildi / mas'ullarda litr; tekshirish belgilari (yoki "Ulanmagan").
4. **KASSA** — qoldiq hozir; kirim/chiqim (davr); davr boshida.
5. **ISH HAQI** — qarz hozir (necha kishiga); hisoblandi / to'landi (davr).
6. **TEKSHIRISH** — e'tibor talab qiladigan holatlar soni (qizil / sariq), 3 tasi ro'yxatda.

Pastida havolali kartalar:
- **Xodimlar xaritada** — Telegram jonli joylashuv + ilova: profil rasmi, rol, dala, vaqt; bosilsa bugungi yo'li.
- **Texnika xaritada (GPS)** — holat: dalada ishlayapti / yo'lda / motor yoniq turibdi / turibdi / aloqa yo'q;
  "N daqiqadan beri bir joyda" ogohlantirish; bugungi yo'l (rangli); salarka **me'yor va berilgan**;
  bajarilgan ishlar (dala · ish turi · ≈ga · %).
- **Bugun bajarilgan ishlar** (GPS): "D-12 · Shudgor ≈ 1.6 ga (69%) · T-01".
- **Dalalar tarixi** — xarita; dalaga bosilsa: 1-/2-/3-terim (kg, reys, ga, %, s/ga) + texnika ishlari
  (shudgor, lazer, kultivatsiya…: sana, ≈ga, soat, texnika).
- **So'nggi hodisalar** lentasi.
Tafsilot sahifalari: Paxta (dalalar/brigadalar/terimchilar/reyslar), Yo'lda (ETA bilan), Reys, Kassa, Ish haqi,
Ishchi, Solyarka, Tekshirish.

## 6. TV EKRANI — `/tv` (ma'lumot: `/tv/data.json`, har 30 s)
Pulsiz! Kalitlar:
- `date, weekday, season, company`
- `kpi`: today (bugun terim), hand, combine, season (mavsum kg), target, target_pct, received (punkt qabul bugun),
  received_n, diff_pct, diff_kg
- `flow`: dalada (soni, kg) → yo'lda (soni, kg) → navbatda (soni, kg) → qabul (soni, kg)
- `trailers`: [{code, state: Dalada/To'ldi/Yo'lda/Navbatda/Qabul/Kutmoqda}]
- `combines`: [{code, operator, kg bugun, oxirgi vaqt}]
- `fields`: [{code, name, ha, kg, kg_ha, poly}] + `fields_stat` (soni, ga, o'rtacha)
- `brigades` (bugun top 6: kg, odam, kg/odam), `hourly` (soatbay bugun va kecha), `workers` (top 5, qisqa ism),
  `fuel` (olindi/berildi litr, tekshirish), `photos` (3 ta), `feed` (10 ta hodisa), `weather` (joy, koordinata).

## 7. TERIM HISOBCHISI (planshet / telefon) — `/dala`
- Bosh sahifa: jonli xarita (qaysi dalada turibdi), yashil — mavsumda terilgan dalalar (kg), "Yangi telashka".
- Yangi telashka: GPS bo'yicha dala avtomatik; telashka, brigadir, qo'l/kombayn, narx (so'm/kg), **terim (1/2/3)**.
- Tortish: ishchi (ro'yxatdan / yangi), kg, GPS holati; internet yo'q bo'lsa navbatga saqlanadi va keyin o'zi yuboriladi.
- Yopish: rasm + **terilgan joyni belgilash** (dala kataklari; qo'shni dalaga o'tsa kg va pul ulush bo'yicha bo'linadi).
- Hujjatlar: nakladnoy (QR bilan), ishchilar PDF, hosil (kg/ga).

## 8. BOSHQA BO'LIMLAR (ma'lumot manbalari)
- **Buxgalteriya:** kassa qoldig'i, bugun terildi, punkt qabul, to'lanadigan ishchilar (soni, so'm), bugun terim puli,
  kombayn qoldig'i, bugungi xarajat, muammolar; **Paxta → Reys bo'yicha:** har reys qo'l terimi kg / odam /
  terimchilar puli / kombayn kg / kombayn puli / jami / holat. Kombaynlar: tarif, hisoblangan, to'langan, qoldiq,
  egalik, salarka.
- **Kassir:** pul oladiganlar, bugun berildi, kassa qoldig'i.
- **Punkt:** kelayotgan telashkalar (ETA), qabul (brutto/tara/netto, farq sababi), QR skaner.
- **Yoqilg'i:** zapravkadan olish (QR), texnikaga berish (QR + rasm + **ish turi**), tiketlar, FIFO narx, tekshirish belgilari;
  har berish uchun "**bu solyarka qayerda ishlatildi**" (GPS).
- **Dalalar:** kontur (KML/GeoJSON), maydon, brigadir, mavsum kg, rasm, tarix. **Dala hosili:** har terim kg, ga, s/ga.
- **Kuzatuv (Telegram):** odamlardan rasm/video so'rash, kechikish.
- **Hisobotlar:** PDF/Excel; Google Sheets ko'zgusi; Telegram hisobot kanali.

## 9. Holatlar lug'ati (belgilar va ranglar uchun)
- Telashka reysi: `OCHIQ` (terilmoqda) → `TOLDI` (to'ldi) → `TORTILDI` (nakladnoy chiqdi) ; `BEKOR` (ko'rsatilmaydi)
- Nakladnoy: `YARATILDI` (yo'lda) → `QABUL` (punkt qabul qildi) ; `BEKOR` (ko'rsatilmaydi)
- Texnika (GPS): dalada ishlayapti 🟢 · yo'lda 🔵 · motor yoniq turibdi 🟠 · turibdi ⚪ · aloqa yo'q ⚫
- Terimlar: 1-terim yashil · 2-terim moviy · 3-terim binafsha. Texnika ishi — to'q sariq.
- Farq: ≤1% yashil (normal) · 1–3% sariq · >3% qizil ("Katta farq").

## 10. Dizayndan kutilayotgan natija
1. **Bosh dashboard** — kompyuter (1920) va planshet (yotiq 1280, tik 800) uchun.
2. **Direktor paneli** — telefon (390) va planshet (800/1280) uchun.
3. **TV** — 1920×1080, uzoqdan o'qiladigan.
4. Har biri HTML+CSS (yoki aniq rasm + ranglar/o'lchamlar) — yuqoridagi ma'lumotlar nomi bilan.
