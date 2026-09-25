# Daladan punktgacha: telashka, nakladnoy, punkt tarozisi

## Jarayon

1. **Hisobchi telashka ochadi.** U faqat dala, brigada va transportni tanlaydi. Telashka raqamini (`TL-2026-000026`) server o‘zi beradi. Raqam har mavsumda 1 dan boshlanadi, takrorlanmaydi va keyin o‘zgartirilmaydi. Bir nechta hisobchi bir vaqtda ochsa ham raqamlar har xil bo‘ladi.
2. **Terimchilar tortiladi.** Har odamning kg si alohida yoziladi. Ish haqi shu kg lardan hisoblanadi.
3. **TUGATISH.** Telashka yopiladi (lock), jami kg va odamlar soni qotiriladi. Ikki hujjat yaratiladi va status **PUNKTGA YO‘LDA** bo‘ladi:
   - **Punkt uchun nakladnoy (PDF):** telashka raqami, dala, brigada, jo‘nagan vaqt, dala vazni, transport va QR kod. Unda odamlarning ismi, narx va summa yo‘q.
   - **Ichki terim hisoboti (PDF):** har bir terimchi va uning kg si, jami. Bu hujjat faqat korxona uchun.
   - Punkt operatoriga xabar boradi: punkt ekrani 20 soniyada bir yangilanadi va “🚜 Yangi yuk yo‘lda” chiqadi. Telegram ulangan bo‘lsa, u yerga ham xabar keladi.
4. **Punktda (operator, masalan Yunus).** Telashkani 3 xil usulda topish mumkin:
   - QR kodni skaner qilish (ilovadagi “QR skaner” yoki telefonning oddiy kamerasi);
   - telashka raqamini yozish (faqat `26` yozish kifoya);
   - “Yo‘lda kelayotgan yuklar” ro‘yxatidan tanlash.

   Shuning uchun qog‘oz nakladnoy yo‘qolsa ham ish to‘xtamaydi.
5. **KELDI.** Bu ixtiyoriy belgi. Tarozi kiritilsa, KELDI o‘zi ham belgilanadi.
6. **Punkt tarozisi.** Operator kg ni kiritadi. Farq darhol hisoblanadi:
   - 0–1%: **Normal** (yashil), sabab so‘ralmaydi;
   - 1–3%: **Diqqat** (sariq), sabab tanlanadi;
   - 3% dan ortiq: **Katta farq** (qizil), sabab tanlanadi.

   Sabablar bitta tugma bilan tanlanadi: Namlik kamaygan · Musur / begona aralashma · Yo‘lda to‘kilgan / yo‘qotish · Tarozi farqi · Qayta tortildi · Kombayn (dalada taxminiy kg) · Boshqa (izoh bilan). Chegaralarni Sozlamalarda o‘zgartirish mumkin: `punkt_warn_pct`, `punkt_alert_pct`.
7. **QABUL QILINDI.** Tugmani ikki marta bosish yoki internet uzilib qayta yuborish ikkinchi qabulni yaratmaydi. Qabul qilingan yozuvni oddiy xodim o‘zgartira olmaydi. Har bir qadam audit tarixida saqlanadi.

## Kim nimani ko‘radi

- **Punkt operatori** faqat o‘z punktini ko‘radi: Yo‘lda · Keldi · Tarozi · Qabul qilindi · Bugungi tarix. Boshqa sahifalar unga server tomonida yopiq.
- **Admin / Rahbar** barcha punktlarni ko‘radi: “Punkt qabuli” menyusi va “Hisobotlar” bo‘limidagi “Punktlar: dala va punkt farqi” hamda “Farq sabablari” hisobotlari.
- Punkt qo‘shish: **Boshqaruv → Punktlar**. Operatorga login berish: **Foydalanuvchilar**, rol “Punkt operatori”, keyin punktni tanlash.

## Kombayn

Kombayn paxtasini odamlar bo‘yicha tortib bo‘lmaydi. Hisobchi kombayn kg sini taxminiy yozadi, aniq og‘irlik esa punkt tarozisida olinadi. Farq sababi sifatida “Kombayn (dalada taxminiy kg)” ni tanlash mumkin.

## Elektron tarozi

Hozir kg tarozi ekranidan qo‘lda kiritiladi (`scale_adapter = manual`). Tarozi modeli, porti va protokoli ma’lum bo‘lgach, `surxon/scale.py` ga adapter qo‘shiladi. Shunda punkt ekranida “Tarozidan” tugmasi paydo bo‘ladi. Hozircha hech qanday ulanish soxta ko‘rsatilmaydi.

## Eski tartib

Sozlamada `auto_waybill_hand = 0` qilinsa, eski tartib qaytadi: TOLDI, keyin umumiy tarozida brutto va tara, keyin nakladnoy.
