# YORDAMCHI BILIMI — SURXAN-PAXTA.UZ qanday ishlaydi (2026 mavsumi)

Bu fayl tizimdagi yordamchining (Claude) “o‘quv kitobi”. Tizim o‘zgarsa — shu fayl ham yangilanadi.
Batafsil: docs/PUNKT.md, docs/TEZPUL.md, docs/BUXGALTERIYA.md, docs/KUZATUV.md.

## Korxona va yo‘l
SURXON (Taxiatosh Textile) — paxta terimi. Paxta yo‘li: **dala → telashka → punkt (klaster) → PQ-17 → pul**.
- **Qo‘l terimi**: odamlar teradi, starshi (guruh boshlig‘i) o‘z guruhining kg sini starshi taloniga yozadi.
- **Kombayn**: kombayn teradi, egasiga tonna / gektar / kun tarifida to‘lanadi.
- **Telashka** (TL-01, TL-02 …) — paxta tashiydigan tirkama. Har yuki — reys (TL-2026-000026).
- **Brigada** — brigadir nomi bilan (masalan “Juma ota”). Har dala bir brigadaga tegishli (D-04 …).

## Kunlik tartib (qog‘oz rejimi, 2026-10-14 dan)
1. **Dalada telefon ishlatilmaydi.** Telashka to‘lganda kim bo‘lsa ham **dala nakladnoyi (PB)** ni ruchkada to‘ldiradi:
   telashka TL-__, qo‘l yoki kombayn №, har joy uchun qator (brigadirga ✓, dala kodi, kg, imzo), JAMI KG.
   Chap qism (nazorat) traktorchida qoladi — 2–3 kunda bir yig‘iladi. O‘ng qism telashka bilan punktga boradi.
2. **Punktda (Yunus, punkt operatori):** umumiy yuk (UY) varag‘ini ochadi → **📷 DALA NAKLADNOYINI RASMGA OLISH** →
   tizim yozuvni AI bilan o‘qiydi → Yunus tekshiradi (sariq qator = aniq o‘qilmagan yoki dala boshqa brigadirniki) →
   **✔ Qo‘shish**. Har qator alohida reys bo‘lib UY ga qo‘shiladi. Hamma telashka qo‘shilgach — **yopish** → yuk xati,
   brutto / tara → netto. Netto telashkalar kg i bo‘yicha bo‘linadi. Muhrlangan UY varag‘i rasmga olinadi (rasmsiz yopilmaydi).
3. **Kechqurun, kassa oldida (terim hisobchisi yoki kassir):** **🌙 Kechki hisob** → brigadani tanlash → starshi talonining
   QR ini skanerlash → odam soni → varaqlarni rasmga olish (ismlar qora parda bilan yopiladi, AI faqat kg ni o‘qiydi) →
   tekshirish → shofyorni tanlash → **SAQLASH**. Keyin kassir talonni skanerlaydi → **PUL BERILDI**.
4. **Kech soat 23:00 da** direktorga Telegramda kunlik hisobot va **sverka** boradi.

## Kunlik sverka (dala → punkt → pul)
Har brigada uchun 3 raqam, tizim o‘zi hisoblaydi:
- 🌾 **Dala** — dala nakladnoyidagi kg; 🏭 **Punkt** — punkt netto (UY dan ulushi); 💵 **Pul** — talonga yozilgan kg.
- ✅ mos · ⏳ punkt hali tortmagan · 🟡 tekshiring (talon yozilmagan yoki dala va punkt farqi) ·
  🔴 farq: pul punktdan 3% dan ko‘p kg ga yozilgan (yoki mavsum bo‘yicha).
- Joyi: Rahbar → **Kunlik sverka**, direktor panelining tepasida rangli banner.

## Kim nima qiladi (har kun)
**Direktor / rahbar (Direktor paneli — faqat ko‘radi):**
- Ertalab: Direktor paneli → sverka banneri, Tekshirish (qizillar), Yo‘lda.
- Qizil bo‘lsa — kartani ochadi, sababini o‘qiydi, mas’ul odamga aytadi.
- Reys noto‘g‘ri brigadaga yozilgan bo‘lsa: telashka → Hujjatlar → “dala / brigada noto‘g‘rimi? Tuzatish”.

**Punkt operatori (Yunus):**
- Har telashka kelganda: UY → nakladnoyni rasmga olish → tekshirish → Qo‘shish.
- UY to‘lganda: yopish → yuk xati, brutto, tara → UY rasmini olish → qabul.
- Farq 1% dan katta bo‘lsa sabab tanlanadi: namlik, musur, yo‘lda to‘kilgan, tarozi farqi, qayta tortildi, kombayn, boshqa.
- Eski ochiq telashka shu telashkaga yangi nakladnoy qo‘shishga xalaqit beradi — adminga aytiladi (admin bekor qiladi).

**Buxgalter:**
- PQ-17 kelganda: hosil-qabuli.uz da imzolash → klasterga faktura → tizimda Faktura (PQ-17) sahifasida belgilash.
- Kassir yozgan xarajatlarni tasdiqlash (Xarajat → tekshirilmaganlar).
- Kun oxirida **kunni yopish**: kassadagi real pulni sanab kiritish (farq bo‘lsa sabab).
- Kombayn egalariga to‘lov tayyorlash (akt-sverka asosida), ishchilarga to‘lov tayyorlash.
- Buxgalteriya → TEZ-PUL panel: talonlar, berilgan pul, to‘lanmaganlar.

**Kassir:** TEZ-PUL kassa: talon QR → summa → PUL BERILDI. Shofyorlarga: 🚐 Shofyorlar → BERILDI.
Kassir kg yoza olmaydi va o‘zgartira olmaydi.

## Menyu (chap tomondagi ☰)
- **Rahbar:** Nazorat · Yordamchi (kunlik hisobot) · Direktor paneli (telefon) · Mashina ekrani · Kunlik sverka.
- **Dala va paxta:** Terim · Telashkalar · Tarozi · Nakladnoy · Nayman · Punkt qabuli · Dalalar · Dala hosili.
- **Buxgalteriya:** Buxgalteriya (bosh) · TEZ-PUL (talon to‘lovlari) · Shofyorlar · Kassir · Ishchilarga to‘lovlar ·
  To‘lovlar · Xarajat yozish · Kassa kitobi · Tuzatishlar · Faktura (PQ-17) · Akt-sverka va firmalar · Kreditlar va lizing.
- **Boshqaruv (admin):** Xodimlar va loginlar · Foydalanuvchilar · Brigadirlar · Punktlar · Punkt blanklari (PB chop etish) ·
  Kassalar · Mavsumlar · Audit tarixi · Zaxira nusxa · Sozlamalar · Integratsiyalar.
- Punkt operatorida faqat: Punkt · QR skaner · Bugungi tarix.

## Qat’iy qoidalar (tizimning o‘zida)
- Hech narsa o‘chirilmaydi: xato yozuv **bekor** qilinadi va tarixda (audit) qoladi. Bekor qilingan narsa hisobga kirmaydi.
- Kg ikki marta sanalmaydi: dala kg, punkt netto va talon (pul) kg — uch alohida raqam.
- Bir talon — bir to‘lov. Ikki marta bosish ikkinchi to‘lov qilmaydi.
- Yopilgan kunga yozib bo‘lmaydi; tuzatish bugungi sana bilan alohida yoziladi.
- Farq — “Farq” deb ataladi (“o‘g‘irlik”, “kamomad” emas). Sababini aniqlash — odamning ishi.
- Ishchilarning ismlari tashqi AI ga yuborilmaydi (talon varag‘ida ismlar qora parda bilan yopiladi).
- TV / mashina ekranida pul ko‘rsatilmaydi.
- Parol, kalit, token hech qachon chatga yozilmaydi: kalitlar faqat Admin → Integratsiyalar dagi yashirin maydonga.

## Ko‘p beriladigan savollar
- **“Telashka punktga kelmadi”** — Direktor paneli → Yo‘lda; yoki punktda UY ro‘yxati. Nakladnoy rasmi olinmagan bo‘lsa reys yo‘q.
- **“Talon noto‘g‘ri yozildi”** — tortish bekor qilinadi (admin/rahbar), talon qayta kechki hisobda skanerlanadi.
- **“Kassada pul yetmayapti”** — buxgalter kassaga kirim qiladi (Mening kassam → Kirim). Pul yetmasa chiqim bo‘lmaydi.
- **“Sverkada qizil”** — Kunlik sverka → qizil karta: qaysi brigada, nechchi kg farq. Starshi talonining varaq rasmlari va
  dala nakladnoyi rasmi bilan solishtiriladi (TEZ-PUL panel, UY sahifasi).
- **“Nechta nakladnoy yozish kerak?”** — Buxgalteriya → Agroklaster sverkasi → hosil-qabuli.uz jadvali: Excel yuklanadi,
  tepada “N ta nakladnoy yozish kerak” (kun bo‘yicha, yuk xati raqamlari bilan). Pastda “Yo‘lda qolgan reyslar”: xato
  yozilganlarini Admin belgilab bekor qiladi — talonlar (ishchilar puli) bekor bo‘lmaydi, brigadaning boshqa reysiga o‘tadi.
- **“Yangi PB blanka kerak”** — Boshqaruv → Punkt blanklari → yaratish → PDF chop etish.
- **“Yangi xodim”** — Boshqaruv → Xodimlar va loginlar (rol tanlanadi; punkt operatoriga punkt biriktiriladi).

## Yordamchi qayerda ishlaydi
- **Saytda:** har sahifadagi yumaloq paxta tugmasi (direktor, punkt operatori, buxgalter). 🎤 — gapirish.
- **Telegram botda (shaxsiy):** Telegrami tizimga ulangan har bir xodim botga yozadi yoki ovozli xabar yuboradi —
  javob yozuvda, ovozli savolga o‘zbekcha ovozda ham. Ulash: admin “Foydalanuvchilar” sahifasidan kod beradi → botga /start KOD.
- **Ish guruhida:** bot guruhda admin; “Yordamchi, …” deb yoki @bot nomi bilan yozilsa, yoki botning xabariga javob
  (ovozli ham) bo‘lsa — javob beradi. Guruhda faqat kg, telashkalar va tizimdan foydalanish haqida; pul va ismlar — faqat shaxsiy chatda.
- O‘zgarish (xarajatni tasdiqlash, kassani yopish, qarzni yopish) faqat saytda, “✔ Bajarish” bilan.
