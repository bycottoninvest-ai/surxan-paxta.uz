# Loyihani istalgan kompyuterdan davom ettirish (SURXAN-PAXTA.UZ)

Bu fayl — “hamma narsa qayerda” degan savolga javob. Kompyuter almashsa ham, yillar o‘tsa ham shu yerdan boshlanadi.

## 1. Nima qayerda saqlanadi

| Nima | Qayerda | Yo‘qolmasligi uchun |
|---|---|---|
| **Dastur kodi, hujjatlar, eslatmalar** | GitHub: `bycottoninvest-ai/surxan-paxta.uz` (`main`) | GitHub o‘zi saqlaydi; oyda bir marta “Code → Download ZIP” qilib fleshka / Google Drive’ga ham qo‘ying |
| **Serverdagi (ishlayotgan) versiya** | GitHub `production` tarmog‘i | Server undan o‘zi yangilanadi |
| **Ma’lumotlar** (baza, rasmlar, hujjatlar) | Hetzner server `/opt/surxan-paxta.uz/data` | Har kecha zaxira (30 kun) + Hetzner Storage Box (serverdan tashqarida) + Hetzner Backups |
| **Parollar va kalitlar** (.env, Telegram bot, Google, Storage Box, Hetzner) | Faqat serverda va sizning parol daftaringizda | GitHub’ga HECH QACHON qo‘yilmaydi. Parollar menejeri yoki qog‘oz daftar |

Ma’lumotni qo‘lda ham olish mumkin: **Admin → Zaxira** — zaxira faylini yuklab olib fleshka / Google Drive’ga qo‘ying (oyda bir marta tavsiya).

## 2. Claude bilan davom ettirish (istalgan kompyuter)

1. Brauzerda **claude.ai/code** ni oching va o‘sha akkaunt bilan kiring.
2. Loyiha sifatida `bycottoninvest-ai/surxan-paxta.uz` ni tanlang.
3. **“paxta”** deb yozing. Claude `docs/PAXTA_ESLATMA.md` ni o‘qib, qayerda to‘xtaganimizni biladi.

Codex ham shu GitHub’dan ishlaydi (unga ham shu faylni o‘qishni ayting).

## 3. Qoidalar (kim ishlasa ham)

- Serverga chiqarish: faqat egasi **“tasdiqlayman”** degandan keyin `git push origin main:production`.
  Server 5 daqiqada o‘zi yangilanadi (yoki Admin → Zaxira → “Hozir yangilash”); ishlamasa o‘zi eski versiyaga qaytadi.
- Har o‘zgarishdan oldin: `pytest -q` — hammasi o‘tishi shart. Telefonda rasmini ko‘rsatib, tasdiq olish.
- Maxfiy narsalar (parol, kalit, dala koordinatalari fayli) GitHub’ga qo‘yilmaydi — repozitoriy ochiq.
- Ishlayotgan qismlarni buzmaslik; bekor qilinganlar hech qayerda hisoblanmaydi; direktor paneli faqat ko‘rish uchun;
  “o‘g‘irlik / kamomad” so‘zlari ishlatilmaydi; TV’da pul yo‘q.
- Batafsil: `CLAUDE.md`, `docs/PAXTA_ESLATMA.md`, `docs/CODEX_YANGILASH.md`, `docs/DIZAYN_MALUMOTLAR.md`.

## 4. Server buzilsa (eng yomon holat)

Yangi Hetzner server → `tools/server_ornatish.sh` bilan o‘rnatiladi → Storage Box’dagi oxirgi zaxiradan ma’lumot
tiklanadi → domen yangi IP ga yo‘naltiriladi. Buni Claude/Codex qadamma-qadam yozib beradi.
