# CLAUDE.md — SURXON PAXTA loyihasi uchun ko‘rsatma

## “paxta” kalit so‘zi

Foydalanuvchi **“paxta”** desa (yoki shunga o‘xshash: “paxtani och”, “paxta loyihasi”), bu — **shu loyihani oldingi joyidan davom ettirish** degani. Darhol:

1. `docs/PAXTA_ESLATMA.md` ni to‘liq o‘qing — oldingi ish tarixi, qarorlar, joriy holat, foydalanuvchi haqida.
2. `docs/TEKSHIRUV.md` (nima bajarilgan/bajarilmagan) va `docs/OCHIQ_MASALALAR.md` (kutilayotgan biznes ma’lumotlari) ni o‘qing.
3. `git log --oneline -15` bilan so‘nggi o‘zgarishlarni ko‘ring.
4. Foydalanuvchiga **o‘zbek tilida**, qisqa qilib: loyiha qayerda turgani, oxirgi qilingan ish va keyingi 2–3 qadamni ayting, keyin davom eting.

## Foydalanuvchi bilan ishlash

- Faqat **o‘zbek tilida** (lotin), sodda, texnik bo‘lmagan so‘zlar bilan yozing. Foydalanuvchi dasturchi emas.
- Har qadamni aniq ayting: qaysi tugma, qaysi fayl, nima yozish. Skrinshot so‘rang.
- Dizayn va texnik qarorlarni o‘zingiz qabul qiling; faqat haqiqiy biznes ma’lumoti yetishmasa so‘rang.
- Ulanmagan narsani “ishlayapti” demang. Test qilinmaganini aniq ayting.
- Maxfiy kalit/token/parolni chatga yozdirmang — faqat serverdagi `.env` ga.

## Loyiha qoidalari (buzmang)

- Kilogramm ikki marta hisoblanmaydi: dala kg, tarozi netto, Nayman qabuli — alohida.
- Har terim aniq reysga (`load_id`) bog‘langan; har biznes amal `tx()` ichida, audit bilan.
- Hech narsa o‘chirilmaydi (soft-void + sabab); audit o‘zgarmas.
- Narx/qoldiq noma’lum bo‘lsa “hisoblanmagan” — 0 emas.
- Rol tekshiruvi `services.py` da (`_need`), web va Telegram uchun bir xil.
- Nayman PDF nusxasida narx/summa yo‘q.
- Namunaviy ma’lumot faqat `APP_MODE=test` va `data-test/` da.
- Har o‘zgarishdan keyin: `pytest -q` (hozir 113 test o‘tadi), keyin commit + push (`main`).

## Tez buyruqlar

```bash
pip install -r requirements-dev.txt
pytest -q                          # testlar
python tools/run_local.py          # test rejimi (data-test/), brauzer ochiladi
```

Windows: `start_test.bat`. Agar `python` Microsoft Store yorlig‘iga tushsa — `py -3` ishlating.
