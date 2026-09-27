# Mashina ekrani (`surxan-paxta.uz/m`): dizayn uchun ma'lumotlar

Bu hujjat dizayner yoki Codex uchun. Ekranning ko'rinishi (rang, joylashuv, shrift) o'zgartirilishi mumkin.
Lekin **ma'lumotlar, havolalar va hisob-kitob o'zgartirilmaydi**. Hammasi quyidagi JSON'dan olinadi.

## Qayerda ishlaydi
- Direktorning mashinasi (BYD Song Plus monitori), planshet yoki istalgan katta ekran.
- **Gorizontal** holat asosiy: taxminan 1920×1080. Ekran tik holatga burilsa, tik holatda ham to'g'ri ko'rinishi kerak.
- Haydovchi uzoqdan va bir qarashda ko'radi:
  - raqamlar **juda katta** (kamida 36px);
  - matn qisqa;
  - fon qorong'i (kechasi ko'zni qamashtirmaydi);
  - tugmalar katta (barmoq bilan bosiladi).
- Kirish: direktor yoki admin logini bilan, "Eslab qol" belgisi qo'yiladi.
- Har 30 soniyada o'zi yangilanadi.
- **Pul hech qachon ko'rsatilmaydi.**

## Ma'lumot manbasi
`GET /m/data.json` (sahifa ochilganda ham shu ma'lumot sahifa ichida bo'ladi):

| Maydon | Ma'nosi |
|---|---|
| `at` | server vaqti `HH:MM:SS` (soat shu bo'yicha ko'rsatiladi) |
| `date`, `weekday` | sana `YYYY-MM-DD`, hafta kuni (o'zbekcha) |
| `kpi.today` | bugun terilgan paxta, kg |
| `kpi.hand`, `kpi.combine` | shundan qo'l terimi, kombayn (kg) |
| `kpi.season` | mavsum jami, kg |
| `kpi.received`, `kpi.received_n` | bugun punkt qabul qilgan kg va reyslar soni |
| `kpi.target`, `kpi.target_pct` | mavsum rejasi va bajarilishi % (reja kiritilmagan bo'lsa `null`) |
| `flow.dalada / yolda / navbat / qabul` | telashkalar: dalada to'lmoqda / yo'lda / punktda navbatda / bugun qabul qilindi (soni) |
| `flow.*_kg` | shu telashkalardagi kg |
| `people[]` | xodimlar: `name` (to'liq ism), `role`, `avatar` (rasm yo'li yoki `null`, unda ismning bosh harfi), `lat`, `lon`, `field` (qaysi dalada), `at` (oxirgi vaqt), `age_min`, `stale` (true bo'lsa joylashuv eskirgan, kulrang) |
| `machines[]` | GPS'li texnika: `code` (T-01, K-01), `kind` (traktor / kombayn / mashina), `operator`, `lat`, `lon`, `state`, `label` (o'zbekcha holat matni), `field`, `at`, `speed`, `still_min` (necha daqiqa turibdi), `alert` |
| `machines[].state` | `field` dalada ishlayapti (yashil), `road` yo'lda (ko'k), `idle` turibdi, motor yoniq (sariq), `parked` to'xtagan (kulrang), `offline` aloqa yo'q (to'q kulrang) |
| `fields[]` | dalalar: `code`, `name`, `ha`, `kg`, `kg_ha`, `poly` (chegara nuqtalari `[lat, lon]`), `active` (true = bugun ish ketyapti, **sariq, ko'zga tashlanadigan**) |
| `live[]` | bugungi real rasm/videolar: `thumb` (kichik rasm), `full` (to'liq rasm/video), `video` (true/false), `title` ("Terim ketyapti", "Telashka to'ldi"...), `place` (dala / texnika), `time`, `who` |
| `feed[]` | "Nima bo'lyapti" lentasi: `time`, `text` |
| `status` | bot so'rovlari: `asked` (so'raldi), `answered` (javob berdi), `waiting` (kutilmoqda), `late` (kechikdi) |

## Hozirgi joylashuv (o'zgartirish mumkin)
1. **Tepa qator:** SURXON logosi (o'zgarmaydi), "jonli" yozuvi, oxirgi yangilanish vaqti, katta soat.
2. **Chapda katta xarita** (ekranning ~2/3 qismi): sun'iy yo'ldosh fotosurati.
   - Band dalalar sariq rangda.
   - Odamlar: dumaloq rasm, tepasida to'liq ismi.
   - Texnika: belgi (🚜 / 🌾), tepasida kodi va haydovchisi.
   - Xarita ish ketayotgan joyga o'zi yaqinlashadi.
   - "Odamlar / Texnika" qatlamlarini yoqib-o'chirish mumkin.
   - Biror narsaga bosilsa, uning bugungi yo'li chiziladi.
3. **O'ngda:**
   - 4 ta katta raqam: bugun, mavsum, dalada, punkt qabuli.
   - "Bugungi real voqealar": 6 ta rasm. Bosilsa kattalashadi, video o'ynaydi.
   - "Nima bo'lyapti" lentasi.

## Qoidalar (buzilmasin)
- Ma'lumot faqat `/m/data.json` dan olinadi. Hech narsa o'ylab topilmaydi va namunaviy raqam qo'yilmaydi.
- Bekor qilingan reyslar hech qayerda ko'rinmaydi. Buni server o'zi hisobga oladi.
- Pul, kassa va narxlar ko'rinmaydi.
- Video faqat bosilganda yuklanadi. Kichik rasm oldindan ko'rinadi, internet trafigi tejaladi.
- SURXON logosi va nomi o'zgarmaydi.
- Dizaynda ishlatiladigan fayllar: `templates/car.html` (sahifa) va `static/js/staffmap.js` (xarita). Xarita logikasiga ehtiyot bo'lib tegiladi.
