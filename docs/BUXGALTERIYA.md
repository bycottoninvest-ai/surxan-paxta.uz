# Buxgalteriya va kassa

Asosiy ma'lumot bazasi — SURXAN-PAXTA.UZ serveri. Google Sheets va Telegram ma'lumotlarning nusxasini oladi xolos.

## Buxgalter nimani yozadi, nima o'zi keladi

**O'zi keladi (qayta yozilmaydi):**
- har bir odamning kg si, dalasi, brigadasi, sanasi va telashkasi (dala hisobchisi yozadi);
- **o'sha tortish paytidagi narx** va summa;
- punktda qabul qilingan kg, farq va sabab (punkt operatori yozadi);
- kombaynning kg si (tonna tarifida) yoki ishlagan kunlari (kunlik tarifda).

**Buxgalter yozadi:**
- kassaga kirim;
- xarajat;
- to'lovni tayyorlash;
- kombayn tarifi, gektar tarifida esa bajarilgan ish hajmi;
- qarzlar;
- kun yopilganda kassadagi real pul.

## Narx tarixi
Har bir tortishda `rate` (narx) va `amount` (summa) alohida saqlanadi. Masalan: 25.09 kuni 100 kg × 1 500 = 150 000, 26.09 kuni 100 kg × 1 700 = 170 000. Narx keyin o'zgarsa ham eski yozuvlar qayta hisoblanmaydi.

Narx kiritilmagan paytdagi kg **"hisoblanmagan"** deb ko'rsatiladi, 0 so'm deb emas.

## To'lov holatlari
**HISOBLANDI → TO'LOVGA TAYYOR → QISMAN TO'LANDI → TO'LANDI.** Qo'shimcha holat: AVANS ORTIQCHA.

1. Buxgalter "339 000 SO'M TO'LASH" tugmasini bosadi. To'lov buyrug'i yaratiladi (PAY-2026-000001), holati TAYYOR.
2. Kassir ro'yxatda ishchini topib, "339 000 SO'M BERILDI" tugmasini bosadi. Pul kassadan chiqim bo'lib yoziladi va kimga, qancha, qachon, qaysi kassir, qaysi kassadan berilgani saqlanadi.
3. Tugmani ikki marta bosish ikkinchi to'lov yaratmaydi.
4. Qarzdan ortiq to'lov tayyorlab bo'lmaydi. Bir odamga bir vaqtda faqat bitta ochiq to'lov bo'ladi.
5. Xato berilgan pul qaytarilsa, **yangi** "qaytarildi" yozuvi qilinadi. Asl to'lovni o'zgartirib bo'lmaydi.

**Avans** ham shu yo'l bilan beriladi. Keyingi hisobdan u avtomatik ayriladi: 500 000 − 150 000 = 350 000 to'lanadi.

## Kassa
Qoldiq har bir kassa bo'yicha shunday hisoblanadi: **KIRIM − CHIQIM = QOLDIQ**.
- Har bir kirim yangi yozuv bo'ladi (INC-…), eski kirim o'zgartirilmaydi.
- Kassada yetarli pul bo'lmasa, chiqim qilib bo'lmaydi.
- Kassir faqat o'z kassasidan pul beradi (Admin → Kassalar, Foydalanuvchilar).

## Kunni yopish
Kun oxirida hisob shunday bo'ladi: boshlang'ich qoldiq + kirim − chiqim = tizim bo'yicha qoldiq. Buxgalter kassadagi real pulni sanab kiritadi:
- farq bo'lsa, sabab tanlanadi va farq ADJ-… yozuvi bo'lib kassa kitobiga tushadi;
- yopilgan kunga endi yozib ham, o'zgartirib ham bo'lmaydi;
- tuzatish bugungi sana bilan alohida yoziladi.

## Kombayn
Tarif har bir kombayn uchun alohida belgilanadi: **tonnaga**, **gektarga** yoki **kunlik**.
- Tonna tarifida summa har bir tortish yozilganda qotadi.
- Kunlik tarifda kombayn birinchi marta yozilgan kun avtomatik hisobga olinadi.
- Gektar tarifida bajarilgan ish hajmini buxgalter kiritadi.

Tarif o'zgarsa, oldin yozilgan ish eski tarifida qoladi. Kombaynga to'lov ham ishchilarniki kabi: buxgalter tayyorlaydi, kassir beradi.

## Xarajat
Telefonda yozish 5–10 soniya oladi: tur (katta tugma) → summa → dala, punkt yoki texnika (ixtiyoriy) → chek rasmi (ixtiyoriy) → SAQLASH. Sana o'zi qo'yiladi. "Kombayn", "Ish haqi" va "Avans" tugmalari o'z bo'limiga olib boradi, chunki bu pul aniq odam yoki kombayn hisobidan yechiladi.

Kassir yozgan xarajat "tekshirilmagan" holatda turadi, buxgalter uni bir tugma bilan tasdiqlaydi.

## Debitor / kreditor
Ikki ro'yxat bor: **biz olishimiz kerak** va **biz berishimiz kerak**. Har bir yozuvda kim, summa, sabab, sana va muddat bor. To'lov kassa orqali yoziladi va qolgan summa o'zi hisoblanadi.

## Hisobotlar
Davr tanlanadi: bugun, kecha, hafta, oy, mavsum yoki o'zingiz tanlagan sanalar. Hisobotda:
- terilgan kg, punkt qabul, farq;
- terimchilar puli, kombayn;
- xarajatlar turlari bo'yicha;
- kassa kirimi, chiqimi va qoldig'i.

Hisobot ekranda, PDF va Excel ko'rinishida olinadi.

## Nusxalar (asosiy baza — server)
- **Google Sheets:** varaqlar KASSA KIRIM-CHIQIM, TO'LOVLAR, AVANSLAR, XARAJATLAR, TERIMCHILAR, PAXTA-PUNKT, DEBITOR-KREDITOR, KUNLIK-YOPILISH, NAYMAN TO'LOVLARI. Har bir qator o'z ID si bilan yangilanadi, shuning uchun qayta urinishda qator takrorlanmaydi.
- **Telegram hisobot kanali:** bu kanalga faqat tizim yozadi. Kunlik hisobot belgilangan vaqtda (sozlama `report_time`, odatda 21:00) yoki kun yopilganda keladi. Ogohlantirishlar ham keladi: katta kg farqi, kassa farqi, tasdiqlanmagan xarajat.
- **Zaxira:** har kecha baza nusxasi, barcha jadvallarning CSV arxivi (Excel'da ochiladi), rasmlar va PDF lar olinadi. `rclone` sozlansa, ular serverdan tashqariga ham ko'chiriladi.

## Xavfsizlik
Tizim bankka ulanmaydi, bank ma'lumotlarini saqlamaydi va o'zi pul jo'natmaydi. U faqat qo'lda berilgan naqd pulning hisobini yuritadi.

Moliyaviy yozuv o'chirilmaydi: xato bo'lsa, "bekor" holati sabab bilan qo'yiladi yoki tuzatish yozuvi qilinadi. Audit jurnalida kim, nima, qachon, eski qiymat, yangi qiymat va sabab saqlanadi.
