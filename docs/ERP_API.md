# Azizbek ERP — faqat o‘qish API (v1)

SURXON PAXTA tizimidagi ma’lumotlarni Azizbek ERP o‘qib, tushuntirib berishi uchun. API **faqat o‘qiydi**: kiritish, tahrirlash va o‘chirish uchun endpoint yo‘q. `POST/PUT/PATCH/DELETE` so‘rovlariga `405 read_only` qaytadi.

## Ulanish

1. Admin: **Sozlamalar → Integratsiyalar**.
2. “Azizbek ERP” ulanishini **Yoqish**.
3. Ruxsat berilgan ma’lumot turlarini belgilab, **Yangi ERP kalit** yarating. Kalit bir marta ko‘rsatiladi. Serverda faqat uning SHA-256 xeshi saqlanadi.
4. ERP har bir so‘rovda sarlavha yuboradi:

```
Authorization: Bearer spx_erp_xxxxxxxx_...
```

Integratsiyalar sahifasida quyidagilar bor: kalitni **almashtirish** (eski kalit darhol o‘chadi), **bekor qilish**, ruxsatlarni o‘zgartirish, ulanishni butunlay o‘chirish va oxirgi 100 ta murojaat tarixi (vaqt, so‘rov, javob kodi, yozuvlar soni, IP).

Limit: bir kalitga daqiqasiga 120 ta so‘rov (`429 rate_limited`).

## Ruxsatlar (scopes)

| scope | Nima beradi |
|---|---|
| `summary` | `/summary` — kunlik va mavsum ko‘rsatkichlari |
| `reference` | `/fields`, `/brigadiers`, `/equipment` |
| `harvests` | `/harvests` — daladagi terim yozuvlari |
| `workers` | Terim va kassa yozuvlarida ishchi **ismi** (shaxsiy ma’lumot, alohida beriladi) |
| `loads` | `/loads` — telashka reyslari |
| `weighings` | `/weighings` — brutto / tara / netto |
| `waybills` | `/waybills` — nakladnoylar |
| `nayman` | `/nayman-receipts` — Nayman qabuli |
| `receivables` | `/receivables` — Naymandan qarzdorlik; `summary` ichida qarz bloki |
| `payments` | `/payments` |
| `expenses` | `/expenses` |
| `cash` | `/cash-entries`, `/cash-balance`, `/cash-days`; `summary` ichida kassa bloki |
| `payouts` | `/worker-balances`, `/payouts`, `/combines` — hisoblangan / to‘langan / qoldiq, to‘lov buyruqlari |
| `debts` | `/debts` — debitor / kreditor |

## Umumiy parametrlar (ro‘yxat endpointlari)

| Parametr | Ma’nosi |
|---|---|
| `season` | Mavsum yili (standart: joriy mavsum) |
| `date_from`, `date_to` | `YYYY-MM-DD`, chegara kiradi |
| `field_id` | Dala bo‘yicha (terim, reys, tarozi, nakladnoy, Nayman, qarz, xarajat) |
| `updated_since` | `YYYY-MM-DD HH:MM:SS` — shu vaqtdan keyin **yaratilgan yoki o‘zgargan** yozuvlar (chegara kiradi, `>=`) |
| `page`, `per_page` | Sahifalash; `per_page` ≤ 500 (standart 100) |

## Javob tuzilmasi

```json
{
  "meta": {
    "api_version": "1",
    "generated_at": "2026-09-25 15:10:02",
    "timezone": "Asia/Tashkent (UTC+05:00)",
    "units": {"mass": "kg", "money": "UZS (so‘m, butun son)", "area": "ga (gektar)", "yield": "kg/ga", "price": "so‘m/kg"},
    "read_only": true,
    "filters": {"season": 2026, "date_from": "2026-09-01"},
    "page": 1, "per_page": 100, "total": 231, "has_more": true, "next_page": 2,
    "max_updated_at": "2026-09-25 15:09:41"
  },
  "data": [ ... ]
}
```

**Sinxronlash:** har so‘rovdan keyin `max_updated_at` ni saqlang. Keyingi so‘rovda uni `updated_since` sifatida bering. Chegara kiradi, shuning uchun yozuvlarni `id` bo‘yicha yangilang (upsert). Bekor qilingan yozuvlar o‘chirilmaydi: `is_voided: true`, `voided_at`, `void_reason` bilan qaytadi.

## Uch xil og‘irlik — qo‘shib bo‘lmaydi

| Maydon | Qayerdan | Ma’nosi |
|---|---|---|
| `field_kg` / `harvests.kg` / `loads.internal_kg` | Dala tarozisi | Ishchi va kombayn bo‘yicha ichki hisob |
| `net_kg` (weighings, waybills) | Umumiy tarozi | **Yakuniy og‘irlik** |
| `accepted_kg` | Nayman | Qabul qilingan og‘irlik |

## Hisoblanmagan qiymatlar hech qachon 0 bo‘lmaydi

Narx yoki boshlang‘ich qoldiq noma’lum bo‘lsa, summa `null` qaytadi. Yonida holat va sabab ko‘rsatiladi:

```json
"nayman_debt": {"amount": null, "status": "not_calculated", "reason": "3 ta Nayman qabulida narx kiritilmagan", "priced_amount": 0, "received": 0}
"cash_balance": {"amount": null, "status": "not_calculated", "reason": "Mavsum uchun boshlang‘ich kassa qoldig‘i kiritilmagan"}
```

`/receivables` → `balance_status`: `calculated` | `price_not_set` | `awaiting_acceptance`.
`/waybills` → `status`: `YARATILDI` = yo‘lda, Nayman qabuli kutilmoqda. Bu **kamomad emas**.

## Endpointlar

| GET | scope | Izoh |
|---|---|---|
| `/api/erp/v1/meta` | — | Kalit ma’lumoti, ruxsatlar, mavsumlar, o‘lchov ta’riflari |
| `/api/erp/v1/summary?season=&date=` | summary | Kun ko‘rsatkichlari + (ruxsat bo‘lsa) moliya bloki |
| `/api/erp/v1/fields` | reference | Maydon, brigadir, ichki kg, netto, kg/ga, xarita konturi |
| `/api/erp/v1/brigadiers` | reference | Brigada natijalari |
| `/api/erp/v1/equipment` | reference | Texnika ro‘yxati |
| `/api/erp/v1/harvests` | harvests | Qo‘l/kombayn, kg, dala, reys (`load_id`) |
| `/api/erp/v1/loads` | loads | Reys holati, ichki kg, netto, nakladnoy |
| `/api/erp/v1/weighings` | weighings | Brutto/tara/netto, vaqtlar, farq va sababi |
| `/api/erp/v1/waybills` | waybills | PA-000001… raqamlar |
| `/api/erp/v1/nayman-receipts` | nayman | Qabul kg, farq, sabab |
| `/api/erp/v1/receivables` | receivables | Har nakladnoy: hisoblangan, to‘langan, qoldiq, holat |
| `/api/erp/v1/payments` | payments | Tushumlar |
| `/api/erp/v1/expenses` | expenses | Xarajatlar |
| `/api/erp/v1/cash-entries` | cash | Kassa daftari |
| `/api/erp/v1/cash-balance` | cash | Kassa qoldig‘i yoki `not_calculated` |
| `/api/erp/v1/cash-days` | cash | Kun yopilishlari: tizim, real, farq, sabab |
| `/api/erp/v1/worker-balances?season=` | payouts | Har ishchi: kg, hisoblangan (har tortish o‘z narxida), narxsiz kg, avans, to‘langan, qoldiq, holat |
| `/api/erp/v1/payouts` | payouts | To‘lov buyruqlari: PAY-raqam, TAYYOR / BERILDI / BEKOR |
| `/api/erp/v1/combines?season=` | payouts | Kombayn tarifi, kg, kun, gektar, hisoblangan, to‘langan, qoldiq |
| `/api/erp/v1/debts?season=` | debts | Biz olamiz / biz beramiz, qolgan summa |

`/harvests` da `rate`, `rate_unit`, `amount` — tortish paytidagi narx va summa (keyin qayta hisoblanmaydi). `/cash-entries` va `/expenses` da `doc_no` (INC-/EXP-/PAY-/ADJ-…) — har operatsiyaning o‘zgarmas ID si.

## Xato kodlari

| HTTP | code | Ma’nosi |
|---|---|---|
| 400 | `bad_request` | Parametr noto‘g‘ri (masalan, sana formati) |
| 401 | `invalid_key` | Kalit yo‘q, noto‘g‘ri yoki bekor qilingan |
| 403 | `scope_denied` | Bu ma’lumot turiga ruxsat berilmagan |
| 405 | `read_only` | Yozish urinishi |
| 429 | `rate_limited` | Juda ko‘p so‘rov |
| 503 | `integration_disabled` | Admin ulanishni o‘chirgan |

## Misol

```bash
curl -H "Authorization: Bearer $KEY" \
  "https://surxan-paxta.uz/api/erp/v1/waybills?season=2026&date_from=2026-09-20&per_page=200"
```

## Holat

Interfeys tayyor va avtomatik testlar bilan tekshirilgan (`tests/test_integrations.py`). Haqiqiy Azizbek ERP bilan ulanish uning manzili va ishlash usuli (so‘rov chastotasi, qaysi ma’lumotlar) berilgach sozlanadi.
