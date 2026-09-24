# Ishga tushirish: kompyuterda test, vaqtinchalik HTTPS, domenga o‘tish

## A. O‘z kompyuteringizda TEST rejimi (domen kerak emas)

1. Python 3.11 yoki yangisini o‘rnating: https://www.python.org/downloads/. Windows'da **“Add python.exe to PATH”** belgisini qo‘ying.
2. Loyihani yuklab oling: GitHub'da **Code → Download ZIP**, keyin arxivni oching.
3. Ishga tushiring:
   - **Windows:** `start_test.bat` faylini ikki marta bosing.
   - **Mac:** `start_test.command` faylini ikki marta bosing (birinchi marta: o‘ng tugma → Open).
   - **Linux:** `bash start_test.command`
4. Birinchi marta kutubxonalar o‘rnatiladi (1–3 daqiqa). Keyin brauzer o‘zi ochiladi.

Oynada quyidagicha yozuv chiqadi:

```
Kompyuterda:   http://localhost:5000
Telefonda:     http://192.168.1.25:5000   (telefon shu Wi-Fi da bo‘lsin)
Kirish:        admin / Test2026!  ·  juma, tarozi01, buxgalter, asadbek, rahbar / Demo2026!
```

- **Telefondan kirish:** telefon kompyuter ulangan Wi-Fi'ga ulangan bo‘lsin. Telefon brauzerida “Telefonda” qatoridagi manzilni oching. Windows “Firewall” ruxsat so‘rasa, **Private network** ga ruxsat bering.
- **Test bazasi alohida:** `data-test/` papkasida saqlanadi. Haqiqiy hisob uchun `data/` papkasi ishlatiladi va ular aralashmaydi. Test rejimida har sahifada “TEST REJIMI” yozuvi turadi. Telegram, arxiv kanali, Google Sheets va tashqi zaxira bu rejimda majburan o‘chiriladi. Namunaviy ma’lumotni yozish buyrug‘i faqat test rejimida ishlaydi.
- **Test ma’lumotini tozalash:** `start_test.bat --reset` (Mac/Linux: `bash start_test.command --reset`).
- **To‘xtatish:** oynada `Ctrl+C` bosing yoki oynani yoping.

Test rejimida sahifalar oddiy `http` orqali ochiladi. Shuning uchun telefonda GPS va “ilovani o‘rnatish” (PWA) ishlamasligi mumkin. Terim, TOLDI, rasm, tarozi va hisobotlar to‘liq ishlaydi.

## B. Serverda: domen faollashguncha parolli vaqtinchalik HTTPS

`docs/DEPLOYMENT.md` dagi 3-bosqichni (Docker, `.env`) bajargach:

```bash
cd /opt/surxan-paxta.uz
sudo bash tools/vaqtinchalik_https.sh
```

Skript quyidagilarni bajaradi:

- Serverning IP manzilidan `https://<ip>.sslip.io` ko‘rinishidagi manzil yasaydi. Bu manzil haqiqiy Let’s Encrypt sertifikati bilan ishlaydi, shuning uchun Telegram webhook ham qabul qiladi.
- Butun saytni qo‘shimcha **login va parol** bilan yopadi. Tizimning o‘z logini ham shu parol ortida turadi. Faqat quyidagilar ochiq qoladi, chunki ularning o‘z maxfiy kaliti bor: Telegram webhook, ERP API va `/health`.
- `APP_DOMAIN` ni shu manzilga qo‘yadi, Telegram webhookni o‘rnatadi (token bo‘lsa) va `smoke-check` bilan tekshiradi.
- Haqiqiy baza `./data` papkasida qoladi, hech narsa ko‘chirilmaydi.

## C. Domen faollashgach

1. DNS'da A yozuvini qo‘shing: `surxan-paxta.uz → server IP` (va `www` uchun ham).
2. Serverda quyidagini ishga tushiring:

```bash
sudo bash tools/domen_ulash.sh
```

Skript quyidagilarni bajaradi:
- DNS shu serverga ko‘rsatayotganini tekshiradi (ko‘rsatmasa, hech narsani o‘zgartirmaydi);
- **avval zaxira nusxa oladi**;
- `APP_DOMAIN=surxan-paxta.uz` ni o‘rnatadi va asosiy Caddyfile bilan qayta ishga tushiradi (HTTPS avtomatik);
- Telegram webhookni yangi manzilga o‘tkazadi. Telegram'da kutib turgan xabarlar o‘chirilmaydi;
- `smoke-check` bilan tekshiradi.

`./data` papkasi (baza, rasmlar, PDF, zaxiralar) o‘zgarmaydi. Loginlar, reyslar va hujjatlar joyida qoladi.
