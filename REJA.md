# Uzum sotuvchilari uchun AI kartochka generatori

Versiya: 1.1. Sana: 2026-09-29.
Holat: MVP kodi, testlar va deploy konfiguratsiyasi amalga oshirilgan. Mock AI bilan lokal va Docker staging sinovlari o'tgan; haqiqiy Gemini kaliti, SMTP, domen/VPS va pilot kerak bo'lgan ishlar ochiq.
Bajarish ro'yxati: [TODO.md](TODO.md).

Ushbu hujjat mahsulot va texnik qarorlarning asosiy manbasi. TODO vazifalarning tartibi, bog'liqligi va qabul mezonlarini belgilaydi. Hujjat tayyorligi ilova yoki tashqi tekshiruvlar bajarilganini anglatmaydi.

## 1. Mahsulot va maqsad

Uzum Market'da mahsulot joylaydigan kichik va o'rta sotuvchi 1-5 ta rasm va ma'lum faktlarni beradi. Ilova o'zbekcha va ruscha kartochka loyihasini yaratadi. Sotuvchi faktlarni tekshiradi, tahrirlaydi, saqlaydi va matnni nusxalaydi yoki oddiy XLSX/CSV fayl oladi.

- Muammo: ikki tilda mahsulot matni yozish va takroriy maydonlarni to'ldirishga vaqt ketishi.
- Asosiy qiymat: tekshirish mumkin bo'lgan, tahrirlanadigan kartochka loyihasi. AI chiqishi tasdiqlangan mahsulot ma'lumoti emas.
- Birinchi auditoriya: pilotdagi 10 nafar sotuvchi; bir akkaunt bir sotuvchiga tegishli.
- Sifat maqsadi: 20 nazorat mahsulotidan kamida 17 tasi 3 daqiqagacha tahrir bilan ishlatishga yaroqli; birortasida tasdiqsiz brend, tarkib yoki sertifikat qat'iy fakt sifatida qolmasin.
- Tezlik maqsadi: navbatsiz AI amali uchun median 20 soniyagacha, p95 60 soniyagacha. Bu sinovda o'lchanadigan maqsad, hozirgi kafolat emas. Navbatdagi kutish alohida o'lchanadi.
- Mahsulot signali: birinchi kartochkasini yaratgan 10 pilot foydalanuvchidan kamida 4 tasi keyingi 7 kun ichida boshqa kunda yana kartochka yaratsin. Bu kichik namuna bo'yicha signal, bozor isboti emas.
- Sotuvchilar bilan qo'shimcha intervyular foydali, lekin kodlashni boshlash uchun majburiy shart emas.

## 2. MVP chegarasi va rollar

### MVP tarkibi

- Email/parol bilan ro'yxatdan o'tish, emailni tasdiqlash, kirish/chiqish, parolni tiklash va almashtirish.
- JPEG, PNG, WebP yuklash; mahsulot izohi; ikki tildagi kartochka yaratish.
- Qo'lda tahrirlash, tekshirish belgilarini hal qilish, maydonni yoki butun natijani nusxalash.
- Butun kartochkani yoki bitta ruxsat etilgan maydonni qayta yaratish.
- Tarix, qidiruv, holat filtri, sahifalash, o'chirish, ko'p kartochkali eksport.
- Oylik limitlar, kunlik himoya limiti, davom etayotgan vazifa holati va tushunarli xatolar.
- Profil, ma'lumotlarni o'chirish, xavfsiz saqlash, monitoring va zaxiradan tiklash.

### MVP tarkibiga kirmaydi

To'lov, Uzum API tokenini kiritish, Uzum'ga avtomatik joylash, kafolatlangan Uzum import shabloni, qoldiq/yo'qolgan tovar hisobi, mobil ilova, Telegram bot, jamoa rollari, admin veb-paneli, ruscha interfeys va rasm sifatiga AI bahosi. Faylning texnik xavfsizligini tekshirish esa majburiy.

| Rol | Ruxsat |
| --- | --- |
| Mehmon | Kirish, ro'yxatdan o'tish va parolni tiklash sahifalari; kartochkalarga kirish yo'q. |
| Emaili tasdiqlanmagan foydalanuvchi | Profil va tasdiqlash xatini qayta so'rash; rasm yuklash va AI amallari yo'q. |
| Tasdiqlangan sotuvchi | Faqat o'z kartochkalari, rasmlari, vazifalari, eksporti va limitlari. |
| Operator | Serverdagi cheklangan CLI orqali texnik holat, tiklash va kvotalarni tekshirish; ommaviy admin endpointi yoki foydalanuvchi sifatida kirish yo'q. |

## 3. Sahifalar va UX

Interfeys o'zbekcha. Alohida reklama landing sahifasi MVP uchun kerak emas: `/` mehmonni kirishga, kirgan foydalanuvchini yangi kartochkaga yo'naltiradi.

| Sahifa | Asosiy elementlar va holatlar |
| --- | --- |
| Kirish va ro'yxatdan o'tish | Email, parol, parolni ko'rsatish tugmasi, maydon yonida xato, yuborish holati, tiklash havolasi. |
| Emailni tasdiqlash va parolni tiklash | Xat yuborildi, token eskirgan/ishlatilgan, qayta yuborish va muvaffaqiyat holatlari. |
| Yangi kartochka | 1-5 rasm, tartiblash/o'chirish, izoh, AI xizmatiga yuborishga rozilik, qolgan limit, yaratish tugmasi. |
| Kartochka | Rasmlar, o'zbekcha/ruscha tablar, maydonli muharrir, tekshirish belgisi, saqlash, nusxalash, qayta yaratish va eksport. |
| Tarix | Matn qidiruvi, holat filtri, sana bo'yicha tartib, 20 tadan sahifalash, thumbnail, sarlavha, holat, checkbox va eksport. |
| Profil | Email/tasdiq, 20 ta yangi kartochka va 40 ta qayta yaratish kvotasi, yangilanish sanasi, parolni almashtirish, akkauntni o'chirish. |
| Umumiy holatlar | Bo'sh tarix, yuklanish, internet uzilishi, 404, xizmat vaqtincha ishlamasligi, limit tugashi, saqlash konflikti. |

Vizual yo'nalish: ishga mo'ljallangan ixcham interfeys, oq asos, grafit matn, yashil asosiy amallar, sariq tekshirish holati, qizil xato. CSS tokenlari bir joyda; ortiqcha gradient, yirik hero va ichma-ich kartalar yo'q. Manrope shriftining lokal nusxasi, odatiy matn 14-16 px, bo'lim sarlavhalari 20-24 px; harflar oralig'i 0.

Desktop'da 220 px navigatsiya va 1200 px gacha kontent; kartochkada rasm ustuni va keng muharrir. Mobil qurilmada bir ustun, rasmlar yuqorida, uzun amallar menyuda. 360, 390, 768 va 1440 px kengliklarda matn/tugmalar to'qnashmasin. Rasm uchun barqaror aspect-ratio; mahsulot kesilmasin (`object-fit: contain`).

Nusxalash, o'chirish, yuklab olish kabi asboblarda Lucide ikonkalari, tooltip va accessible name; til uchun tab, ko'p tanlash uchun checkbox. Klaviatura fokusi ko'rinsin, holat faqat rang bilan ifodalanmasin, touch nishoni kamida 44 px. Muhim xato va natija `aria-live` orqali e'lon qilinsin. Animatsiya faqat yuklanish/holat almashishida, reduced-motion qo'llab-quvvatlanadi.

### Asosiy oqim

1. Ro'yxatdan o'tish, emailni tasdiqlash, kirish.
2. Rasm va izoh kiritish; lokal preview, fayl xatolarini ko'rish.
3. Server draft yaratadi; keyingi so'rov kvota band qilib AI vazifasini navbatga qo'yadi. Ikkinchi so'rov rad etilsa draft yo'qolmaydi.
4. Holat har 2 soniyada tekshiriladi. Brauzer yopilsa ham server vazifasi davom etadi; qaytib kirganda holat bazadan olinadi.
5. Natijani tekshirish va tahrirlash; saqlash aniq tugma bilan. Avtomatik saqlash MVP'da yo'q.
6. Nusxalash yoki eksport; tarixdan qayta ochish.

### Muharrirning muhim qoidalari

- Saqlanmagan tahrir bilan chiqish, qayta yaratish yoki eksport qilishda saqlash/bekor qilish tanlovi ko'rsatiladi.
- Nusxalash muharrirda ko'rinayotgan joriy matnni oladi; eksport faqat serverda saqlangan versiyani oladi.
- AI vazifasi faol paytda ushbu kartochkani saqlash, yana yaratish, eksport va o'chirish bloklanadi; boshqa kartochkalarni ko'rish mumkin.
- Bitta maydon qayta yaratilsa boshqa maydonlar va ularning tasdiqlari o'zgarmaydi. Butun kartochka qayta yaratilishidan oldin almashtirish tasdiqlanadi.
- Qayta yaratish xato bo'lsa oldingi muvaffaqiyatli matn saqlanadi. Noto'g'ri yangi javob eski matn ustiga yozilmaydi.
- Ikki tabdan saqlashda eskirgan `version` uchun 409 qaytadi; foydalanuvchining lokal matni saqlanib, serverdagi yangi versiya bilan solishtirish mumkin bo'ladi.
- Foizli soxta progress yo'q: `Navbatda`, `Yaratilmoqda`, `Tayyor`, `Xato` holatlari ko'rsatiladi. Bekor qilish faqat navbatdagi vazifa uchun.

## 4. Kartochka ma'lumotlari shartnomasi

`CardContent` sxemasi `schema_version=1`. O'zbekcha matn lotin yozuvida, ruscha matn alohida. Saqlash, AI tekshiruvi va eksport bir xil sxemadan foydalanadi; noma'lum JSON kalitlar qabul qilinmaydi.

| Maydon | Turi va ilovaning ichki chegarasi |
| --- | --- |
| `title.uz`, `title.ru` | Bo'sh bo'lmagan matn; har biri 200 belgigacha. |
| `short_description.uz`, `.ru` | Matn; har biri 1000 belgigacha. |
| `description.uz`, `.ru` | Bo'sh bo'lmagan oddiy matn; har biri 10000 belgigacha. HTML qabul qilinmaydi. |
| `attributes` | 0-30 element: `key` (barqaror texnik nom, 64), ikki tilli `name` (100) va `value` (500), `source`. `key` lar takrorlanmaydi. |
| `suggested_category.uz`, `.ru` | Matn yoki `null`, 200 belgigacha. Uzum kategoriya ID'si emas, faqat taklif. |
| `keywords.uz`, `.ru` | Har tilda 0-20 noyob matn, bittasi 64 belgigacha. |
| `color.uz`, `.ru` | Matn yoki `null`, 100 belgigacha. |
| `material.uz`, `.ru` | Matn yoki `null`, 150 belgigacha. Rasmdan materialni qat'iy tasdiqlash mumkin emas. |
| `review_items` | 0-50 element: `id`, `path`, `reason`, `source`; faqat haqiqiy maydonlarga havola. |

Bu sonlar xavfsiz ishlash uchun ichki chegaralar, Uzumning rasmiy limitlari emas. T02 natijasidagi tekshirilgan qoidalar alohida versiyalangan qoida faylida saqlanadi; amaldagi limit ichki va rasmiy limitning kichigi bo'ladi. Qoidalar tekshirilmaguncha mahsulotga "Uzum moderatsiyasidan kafolatli o'tadi" deyilmaydi.

`source`: `seller`, `visible`, `inferred`, `unknown`. Bu AI ko'rsatgan manba toifasi, kalibrlangan ishonch foizi emas. Brend, tarkib, sertifikat, o'lcham, kafolat va sog'liq haqidagi da'vo sotuvchi ma'lumoti yoki aniq o'qiladigan dalilsiz fakt sifatida berilmaydi. Noma'lum ixtiyoriy maydon `null` qoladi; narx generatsiya/eksport sxemasiga kirmaydi.

Sotuvchi tekshirish belgisini qiymatni tasdiqlash yoki noma'lum ixtiyoriy qiymatni bo'sh qoldirish orqali hal qiladi. Qaror serverda foydalanuvchi, vaqt va maydon qiymatining xeshi bilan saqlanadi. Qiymat o'zgarsa shu tasdiq bekor bo'ladi. Generatsiya bo'yicha yangi tekshirish belgilari ham hal qilinadi. Tasdiq AI'ning JSON javobidan qabul qilinmaydi.

`review_items`, manba metadata'si va tasdiq vaqti server boshqaradigan maydonlardir. PATCH faqat tahrirlanadigan qiymatlar va ruxsat etilgan review qarorlarini oladi; client warning ro'yxatini o'chirib yoki metadata yuborib tekshiruvni chetlab o'ta olmaydi.

Qayta yaratiladigan `field_path` lar: `title.uz`, `title.ru`, `short_description.uz`, `short_description.ru`, `description.uz`, `description.ru`, `attributes`, `suggested_category`, `keywords.uz`, `keywords.ru`, `color`, `material`. Massiv ichidagi ixtiyoriy indeks yoki boshqa JSON yo'li qabul qilinmaydi. Murakkab maydon tanlansa ikki tilli obyekt birgalikda yangilanadi.

Initial/all AI javobi to'liq `CardContent`; bitta maydon javobi `FieldGenerationResult`: `field_path`, `value`, `review_items`. `field_path` so'ralgan qiymatga aynan teng, `value` o'sha maydon sxemasida, review yo'llari faqat o'sha maydon ostida bo'lishi shart. Bunday javobdan qolgan kontent qayta tuzilmaydi, faqat tekshirilgan maydon qo'llanadi.

API'da kartochka konverti: `id`, `version`, `status`, `seller_notes`, `content`, `images`, `latest_job`, `created_at`, `updated_at`. `status` faqat `draft` yoki `ready`; navbat/xato holati vazifadan olinadi. AI'ning oxirgi muvaffaqiyatli asl javobi tahrirlangan `content` dan alohida saqlanadi; to'liq tahrirlar tarixi MVP'da yo'q.

## 5. Kvota va xarajat qoidalari

| Qoida | Aniq qaror |
| --- | --- |
| Yangi kartochka | Bir foydalanuvchiga kalendar oyida 20 ta muvaffaqiyatli dastlabki generatsiya. |
| Qayta yaratish | Alohida kalendar oylik 40 ta muvaffaqiyatli amal; bitta maydon ham, butun kartochka ham shu havzadan 1 ta oladi. |
| Kunlik himoya | Bir kunda jami 10 ta navbatga qabul qilingan AI amali; ikki tur va muvaffaqiyatsiz/bekor qilinganlari ham kiradi. |
| Parallel ish | Foydalanuvchiga bir vaqtda bitta faol AI vazifasi; pilot worker bir vaqtda bitta provider so'rovi bajaradi. |
| Draft yaratish | Bir foydalanuvchiga bir kunda 10 ta yangi draft; takroriy idempotent so'rov qayta sanalmaydi. |
| Bepul amallar | Tahrir, saqlash, nusxalash, ko'rish va eksport kvota sarflamaydi. |
| Davr | `Asia/Tashkent` bo'yicha kalendar oy/kun; bazadagi vaqtlar UTC. Windows uchun `tzdata` bog'liqligi. |

- Yangi kartochka limiti va qayta yaratish limiti alohida: qayta yaratish sabab 20 ta yangi kartochka imkoniyati kamaymaydi.
- Vazifa qabul qilinganda oylik kvota band qilinadi: `available = limit - used - reserved`. Natija va kvotaning `used` ga o'tishi bitta DB tranzaksiyasida.
- Xato yoki navbatda bekor qilish oylik rezervni qaytaradi; kunlik himoya soni qaytmaydi. Provider o'zi xarajat olgan bo'lishi mumkin, bu foydalanuvchi kvotasidan alohida hisob.
- Oylik chegaradan o'tgan vazifa rezerv qilingan davr hisobiga yakunlanadi. Keyingi oy kvotasi eski vazifa sabab o'zgarmaydi.
- Kartochkani o'chirish ishlatilgan kvotani qaytarmaydi. Qo'lda tahrir qilingan yoki eski tayyor kartochka uchun `initial` qayta qabul qilinmaydi.
- Band qilish, kunlik tekshiruv va job yaratish atomar; parallel so'rovlar limitni oshirib yubormasligi shart.
- Har bir AI job uchun eng ko'pi 2 ta provider chaqiruvi va har chaqiruvda 4096 output token. Umumiy xarajat uchun operatorda `AI_DAILY_BUDGET_USD` majburiy sozlama bo'ladi.
- Budjet miqdori T04 dagi model narxi va egasining xarajat chegarasi asosida belgilanadi; o'ylab topilgan narx ishlatilmaydi. Worker rasm/matn input chegarasi va output/thinking xarajati hisobga olingan yuqori chegarani chaqiruvdan oldin band qiladi. Usage noma'lum bo'lsa shu summa konservativ sarf sifatida hisoblanadi, nolga chiqarilmaydi; bu provider hisob-fakturasi emas.
- Har chaqiruvning pul rezervi va yakuni `ai_call_usage` bilan bog'liq; faqat provider chaqirilmagani aniq bo'lsa rezerv to'liq qaytadi. Aniqlangan haqiqiy usage bilan farq atomar yopiladi, worker uzilishida noma'lum urinish ham bir marta konservativ hisobga o'tadi. Pul kuni ham `Asia/Tashkent` bo'yicha, band qilingan davr o'zgarmaydi.
- Global budjet tugasa yangi AI job qabul qilinmaydi, navbatdagilar provayder chaqirilmasdan `budget_exhausted` bilan tugatiladi va oylik rezerv qaytadi. Narx sozlamasi yoki budjet yo'q bo'lsa haqiqiy provider rejimi ishga tushmaydi.

## 6. Ma'lumotlar bazasi

PostgreSQL lokal/integratsiya sinovida ham, serverda ham ishlatiladi. Bu atomar kvota va navbat xatti-harakatini bir xil sinash uchun tanlangan; oldingi SQLite taklifi almashtirildi. UUID identifikatorlar, UTC `timestamptz`, hisoblagichlar uchun manfiy bo'lmaslik cheklovi, xarajatlar uchun `numeric`, matn JSON uchun `jsonb`.

| Jadval | Asosiy ustunlar va cheklovlar |
| --- | --- |
| `users` | `id`, `email_normalized` unique, `password_hash`, `email_verified_at`, `ai_consent_at`, `consent_version`, `created_at`, `deletion_requested_at`. |
| `sessions` | `id`, `user_id` nullable (anonim CSRF uchun), `token_hash` unique, `csrf_secret`, `created_at`, `last_seen_at`, `expires_at`, `revoked_at`. |
| `auth_tokens` | `id`, `user_id`, `purpose` (`verify_email`/`reset_password`), `token_hash` unique, `expires_at`, `used_at`. |
| `cards` | `id`, `user_id`, `creation_key`, `creation_fingerprint`, `status`, `version`, `seller_notes`, `content_json`, `last_ai_json`, `review_resolutions_json`, `schema_version`, `created_at`, `updated_at`, `deleted_at`. Unique `(user_id, creation_key)`. |
| `card_images` | `id`, `card_id`, `storage_key` unique, `sha256`, `mime_type`, `width`, `height`, `size_bytes`, `position`; unique `(card_id, position)`. |
| `generation_jobs` | `id`, `user_id`, `card_id`, `operation`, `field_path`, `base_version`, `input_snapshot_json`, `status`, `idempotency_key`, `request_fingerprint`, `attempt_count`, `error_code`, `provider_model`, `prompt_version`, `rules_version`, `result_json`, `created_at`, `started_at`, `heartbeat_at`, `finished_at`, `worker_id`, `lease_expires_at`, `quota_period`, `quota_bucket`, `quota_state`. |
| `monthly_usage` | `user_id`, `period` (`YYYY-MM`), har ikki havzaning `limit`, `used`, `reserved` hisoblagichlari; unique `(user_id, period)`. |
| `daily_usage` | `user_id`, `day`, `accepted_jobs`, `created_drafts`; unique `(user_id, day)`. |
| `ai_call_usage` | `id`, `job_id`, `attempt_number`, `provider_request_id` nullable, `model`, input/output/thinking tokenlar nullable, `budget_day`, `reserved_cost`, `budget_state` (`reserved/settled`), `estimated_cost`, `cost_basis` (`reported/upper_bound/not_called`), `price_version`, `outcome`, `started_at`, `finished_at`; unique `(job_id, attempt_number)`. |
| `ai_daily_budget` | `day`, `limit_usd`, `spent_usd`, `reserved_usd`; provider xarajati uchun global atomar hisob. |
| `worker_heartbeats` | `worker_id`, `last_seen_at`; operatorga worker tirikligini aniqlash uchun, foydalanuvchiga ochilmaydi. |
| `deletion_events` | `id`, `entity_type` (`user/card`), `entity_id`, `requested_at`, `purged_at`, `exported_at`, `expires_at`; email/matn yo'q, o'chiriladigan obyektga FK yo'q. Tiklash uchun shifrlangan offsite reyestrga chiqariladi. |

`generation_jobs.status`: `queued`, `running`, `succeeded`, `failed`, `cancelled`. `quota_state`: `reserved`, `consumed`, `released`. Unique `(user_id, idempotency_key)` va `queued/running` holatlari uchun `user_id` bo'yicha partial unique index bo'ladi. Bir foydalanuvchining ikki parallel faol job'i DB darajasida ham imkonsiz.

Indekslar: cards `(user_id, deleted_at, updated_at, id)`, jobs `(status, created_at)` va `(status, lease_expires_at)`, auth token expiry, session expiry. Kartochka qidiruvi egaga tegishli sarlavha/izoh bo'yicha `ILIKE`; kichik MVP uchun alohida qidiruv serveri yo'q. Sahifalash tartibi `updated_at DESC, id DESC`, 20 element, eng ko'pi 100.

Foreign key lar va o'chirish tartibi migratsiyalarda aniq bo'ladi. Faylni o'chirish DB rollback bilan qaytmasligi sabab avval soft-delete, keyin qayta bajarilishi xavfsiz purge ishlatiladi. DB migratsiyasi ishlab turgan web/worker startup'ida avtomatik emas, deploy'da bir martalik qadam.

## 7. AI generatsiya shartnomasi

Boshlang'ich model nomzodi Gemini 2.5 Flash; mavjudligi, rasm/structured output qo'llashi va narxi T04 da tekshiriladi. Model `AI_MODEL` orqali tanlanadi. O'z-o'zidan boshqa providerga o'tish yo'q: boshqa xizmatga rasm yuborish alohida tasdiqlanadigan qaror.

1. Serverga tegishli rasm ID'lari va saqlangan sotuvchi izohi olinadi; foydalanuvchi bergan tashqi URL yuklanmaydi.
2. Prompt, sxema va tekshirilgan Uzum qoidalarining versiyasi job snapshot'iga yoziladi.
3. Rasmlar va izoh ko'rsatma emas, ishonchsiz mahsulot ma'lumoti sifatida beriladi. Modelga tool/function calling, fayl yoki tarmoq bajarish huquqi berilmaydi.
4. Tuzilgan JSON olinadi; Pydantic turi, o'lchamlar, tillar, maydon yo'llari va biznes qoidalari serverda tekshiriladi. HTML, script va ortiqcha kalitlar qabul qilinmaydi.
5. Reklama superlativlari, aloqa ma'lumotlari, tasdiqsiz brend/material/tarkib/sertifikat da'volari cheklanadi. Ko'rinmagan mahsulot xossalari o'ylab topilmaydi; qoida tekshiruvi faktlarning to'g'riligini to'liq kafolatlamaydi.
6. 429/5xx yoki sxema buzilishi uchun jami bitta qo'shimcha urinish mumkin. `Retry-After` 15 soniyadan katta bo'lsa kutib xarajat oshirilmaydi. Timeout/noaniq transport uzilishida avtomatik takrorlash yo'q.
7. Har provider chaqiruvi 45 soniyagacha, job bajarilishi jami 120 soniyagacha. Ikkinchi chaqiruv qolgan muddatga sig'masa qilinmaydi.
8. Faqat to'liq tekshirilgan natija qo'llanadi. Maydon regeneratsiyasida faqat whitelist'dagi maydon va unga tegishli review elementlari almashtiriladi.
9. Natija, kartochka versiyasi, job muvaffaqiyati va kvota ishlatilishi bir tranzaksiyada saqlanadi.

Provider adapteri ilova sxemalarini SDK obyektlaridan ajratadi. Testlarda soxta adapter ishlatiladi; normal CI pullik AI chaqirmaydi. Promptga foydalanuvchi paroli, emaili, sessiya yoki API kaliti yuborilmaydi. Xom provider HTTP javobi/logi saqlanmaydi; tekshirilgan JSON va kerakli usage metadata yetarli.

## 8. Uzoq vazifalar va tiklanish

AI uchun FastAPI `BackgroundTasks` ishlatilmaydi: server qayta ishga tushganda xotiradagi vazifa yo'qolishi mumkin. O'rniga PostgreSQL'dagi `generation_jobs` va alohida worker process tanlanadi. Pilotda Redis/Celery qo'shilmaydi. Bu kichik DB navbati shu mahsulot doirasida; umumiy navbat framework'i yozilmaydi.

| O'tish | Natija |
| --- | --- |
| Yangi so'rov -> `queued` | Egalik, email, rozilik, versiya, idempotency va kvota tekshiriladi; snapshot/job/rezerv bir tranzaksiyada. |
| `queued` -> `running` | Worker `FOR UPDATE SKIP LOCKED` bilan claim qiladi; keyin DB tranzaksiyasi yopiladi, provider chaqiruvida lock ushlab turilmaydi. |
| `running` -> `succeeded` | Natija qo'llanadi, kartochka `ready`, `version + 1`, rezerv `consumed`. |
| `queued/running` -> `failed` | Mashina o'qiydigan xato, rezerv `released`; avvalgi tayyor kontent o'zgarmaydi. |
| `queued` -> `cancelled` | Egasi bekor qilishi mumkin; rezerv `released`; provider chaqirilmaydi. |

- Worker navbatni har 2 soniyada tekshiradi; pilotda bitta worker, bir faol job. Global navbat ko'pi bilan 20 job; to'lsa 503, rezerv olinmaydi.
- Worker va faol job heartbeat'i har 15 soniyada; job lease 180 soniya. Qolib ketgan `running` job lease tugagach `worker_interrupted` bilan failed bo'ladi va kvota bir marta qaytariladi.
- Worker tiklanganda `queued` job'lar davom etadi. 10 daqiqadan ko'p navbatda qolgan job `queue_expired` bo'ladi, rezerv qaytadi.
- Nazorat loop'i startup'da va har 30 soniyada ishlaydi; worker butunlay o'chiq bo'lsa tashqi health tekshiruvi ogohlantiradi va process qayta ishga tushiriladi.
- Kech qaytgan provider natijasi faqat job hali `running`, lease haqiqiy va kartochka `base_version` ga teng bo'lsa yoziladi; aks holda tashlab yuboriladi. Terminal job yana muvaffaqiyatga aylantirilmaydi.
- Provider chaqiruvi tashqi tizimda aynan bir marta bajarilishini kafolatlab bo'lmaydi. DB natijasi va foydalanuvchi kvotasi esa takroran yozilmasligi shart.
- Idempotency key bir amal uchun brauzerda bir marta yaratiladi. Bir xil kalit + bir xil fingerprint mavjud natijani qaytaradi; boshqa payload 409. Tekshiruv kvota band qilishdan oldin.
- `initial` faqat hali muvaffaqiyatli AI natijasi bo'lmagan draft uchun; xatodan keyingi qo'lda qayta urinish yangi idempotency key oladi. `regenerate_*` faqat `ready` kartochka uchun.
- Rasmlar draft yaratilgandan so'ng o'zgarmaydi; boshqa rasm to'plami yangi draft hisoblanadi. Izoh/matn faqat faol job bo'lmaganda o'zgaradi.

## 9. HTTP va xizmatlar shartnomasi

Cookie sessiya, mutatsiyalarda CSRF majburiy. UI HTML, `/api/*` JSON qaytaradi; rasm yuklash multipart, rasm va eksport binary. Kichik JS modul upload/save/copy/download bilan, HTMX esa ro'yxat va holat fragmentlari bilan ishlaydi. Har ikkala yo'l bir xil servis qatlamini chaqiradi.

| Metod va yo'l | Kirish va muvaffaqiyat natijasi |
| --- | --- |
| `GET /` | Sessiyaga qarab 303 yo'naltirish. |
| `GET/POST /auth/register` | HTML forma; email/parol; POST har doim bir xil umumiy tasdiqlash xabari bilan 303, mavjud email oshkor qilinmaydi. |
| `GET/POST /auth/login` | HTML forma; muvaffaqiyatda yangi sessiya va 303; umumiy noto'g'ri login xabari. |
| `POST /auth/logout` | Sessiyani bekor qilish, 303 kirish sahifasiga. |
| `GET/POST /auth/verify-email` | GET tokenni tasdiqlash formasi; POST tokenni bir marta ishlatadi, 303. |
| `POST /auth/verification-email` | Tasdiq xatini qayta yuborish, umumiy javob va 303. |
| `GET/POST /auth/forgot-password` | Har email uchun bir xil umumiy javob, 303. |
| `GET/POST /auth/reset-password` | Token, yangi parol; POST tokenni ishlatadi, barcha sessiyalarni bekor qiladi, 303 kirishga. |
| `GET /cards`, `/cards/new`, `/cards/{card_id}`, `/profile` | Egaga tegishli HTML sahifalar. |
| `GET /cards/{card_id}/status` | HTMX holat fragmenti; har 2 soniyada, terminal holatda polling to'xtaydi. |
| `POST /api/cards` | Multipart `images[]`, `seller_notes` (0-2000 belgi), rozilik versiyasi; `Idempotency-Key`; 201 draft konverti. |
| `GET /api/cards` | `q` (0-200), `status` (`draft/ready/queued/running/failed`), `page>=1`, `page_size<=100`; 200 ro'yxat/total/page. |
| `GET /api/cards/{card_id}` | 200 kartochka konverti. |
| `PATCH /api/cards/{card_id}` | `expected_version`, izoh, ixtiyoriy `content` va review qarorlari; 200 saqlangan konvert. Draft'ga qo'lda tayyor AI kontenti kiritish MVP'da yo'q. |
| `DELETE /api/cards/{card_id}` | `expected_version`; faol job yo'q bo'lsa soft-delete, 204. |
| `GET /api/cards/{card_id}/images/{image_id}` | Faqat egaga rasm; `Cache-Control: private, no-store`. |
| `POST /api/cards/{card_id}/generations` | `operation`, zarur bo'lsa `field_path`, `expected_version`; `Idempotency-Key`; 202 `job_id`, `status`, `status_url`, kvota. |
| `GET /api/jobs/{job_id}` | 200 egaga tegishli holat, xato kodi/xabar, kartochka ID'si va natija versiyasi; prompt/raw provider javobi yo'q. |
| `POST /api/jobs/{job_id}/cancel` | Faqat `queued`; 200 cancelled; terminal cancelled takrori ham xavfsiz. |
| `POST /api/exports` | `card_ids` (1-100 noyob UUID), `format` (`xlsx/csv`); 200 attachment. |
| `GET /api/me/usage` | Joriy davr, limit/used/reserved/available, reset va kunlik qoldiq. |
| `POST /api/me/password` | Joriy/yangi parol; barcha sessiyalar bekor bo'ladi, 204. |
| `DELETE /api/me` | Joriy parol va tasdiq; running job bo'lsa 409; queued job'lar bekor, akkaunt darhol yopiladi, purge uchun 202. |
| `GET /health/live`, `/health/ready` | Process liveness va DB/migratsiya readiness; 200 yoki 503, sir/foydalanuvchi ma'lumoti yo'q. |

JSON xatosi: `{"error":{"code":"quota_exceeded","message":"Oylik limit tugagan.","fields":{},"request_id":"..."}}`.

HTTP holatlari: 401 sessiya yo'q, 403 CSRF/email/rozilik talabi, 404 mavjud emas yoki boshqa egaga tegishli obyekt, 409 versiya/faol job/idempotency/holat konflikti, 413 hajm, 415 fayl turi, 422 maydon qoidasi, 429 kvota/rate limit, 503 navbat/provider/budjet vaqtincha mavjud emas. Tegishli 429/503 javobda `Retry-After` yoki kvota yangilanish vaqti bor. Xato javobiga traceback chiqmaydi.

Har bir obyekt so'rovida `user_id` sessiyadan olinadi; request tanasidagi egaga ishonilmaydi. Ommaviy eksportda bitta begona ID ham bo'lsa butun so'rov 404; qisman eksport bilan obyekt mavjudligi oshkor qilinmaydi.

## 10. Xavfsizlik va fayl hayot sikli

- Parol 12-128 belgi, Argon2id; parol, token va CSRF qiymati loglanmaydi. Auth uchun sinovdan o'tgan kutubxonalar ishlatiladi, shifrlash algoritmi yozilmaydi.
- Sessiya tokeni kriptografik tasodifiy 256 bit; bazada faqat xesh. 7 kunlik mutlaq, 24 soatlik bekor turish muddati; login'da token almashadi. Cookie prod'da `Secure`, `HttpOnly`, `SameSite=Lax`, `Path=/`, Domain yo'q.
- CSRF token anonim login/ro'yxatdan o'tish formalari va barcha mutatsiyalarda tekshiriladi. SameSite uning o'rnini bosmaydi. CORS faqat shu origin.
- Tasdiq tokeni 24 soat, parol tiklash tokeni 30 daqiqa, ikkisi ham bir martalik va xeshlangan. Yangi token avvalgisini bekor qiladi. GET havola tokenni sarflamaydi; email skaneri uni ishlatib qo'ymasligi uchun POST talab etiladi.
- Login limitlari: IP bo'yicha 15 daqiqada 20 urinish, email bo'yicha 5 xato; register/forgot/resend IP bo'yicha soatiga 10, manzil bo'yicha 3 xat. Email mavjudligidan qat'i nazar umumiy javob. Limit parolni abadiy bloklamaydi.
- SlowAPI bir web process uchun; pilot `workers=1`. Ko'p web processga o'tishdan oldin rate limit umumiy storage'ga ko'chirilishi shart. Oylik/kunlik AI limitlari boshidan DB'da.
- Faqat ishonchli reverse proxy IP'sining forward headerlari ishlatiladi. Host header whitelist; CSP lokal assetlar, `nosniff`, tokenli sahifalarda `Referrer-Policy: no-referrer`; maxfiy HTML cache qilinmaydi.
- Fayl: har biri 10 MiB, jami 1-5 dona, request 52 MiB gacha. JPEG/PNG/WebP ichki formati dekoder orqali tekshiriladi; nom/kengaytmaga ishonilmaydi. SVG, animatsiya, buzilgan fayl va 40 megapikseldan kattasi rad etiladi.
- Rasm ketma-ket decode qilinadi; EXIF orientation qo'llanadi, uzun tomoni 1024 px gacha kichrayadi, alpha oq fon bilan birlashtiriladi, JPEG sifat 90 ga qayta kodlanadi. EXIF va boshqa metadata tashlanadi. Texnik tekshiruv rasmga tijoriy sifat bahosi berish emas.
- Saqlash yo'li server UUID'si; uploadlar static/public emas. Asl yuklangan fayl qayta ishlangach o'chiriladi; foydalanuvchiga va AI'ga bir xil tozalangan rasm ishlatiladi.
- Upload vaqtinchalik joyga yoziladi; DB/fayl operatsiyasi xatosida tozalanadi. Yetim temp fayllar 24 soat ichida maintenance orqali o'chadi. Disk yetishmasa 503, yarim kartochka yoki kvota sarfi qolmaydi.
- Matn Jinja autoescape bilan ko'rsatiladi; AI HTML yoki Markdown'i bajarilmaydi. Clipboard'ga plain text. SQL parametrli; qidiruv/filtr/sort whitelist bilan.
- AI xizmatiga rasm yuborishdan oldin foydalanuvchi roziligi, provider nomi va ma'lumot maqsadi aniq bo'ladi. Provider ma'lumot saqlashi/trening sharti tekshirilmasdan "saqlanmaydi" deb va'da qilinmaydi.
- Muvaffaqiyatsiz yoki generatsiyasiz draft 7 kundan so'ng tozalanadi; faol job'li draft tozalanmaydi. Tayyor kartochka egasi o'chirguncha saqlanadi. UI draft muddati va o'chirish oqibatini ko'rsatadi.
- Soft-delete qilingan kartochka darhol ko'rinmaydi; uning rasmlari, JSON natijalari va snapshotlari 24 soatda purge qilinadi. Shaxsiy matnsiz usage hisoblagichi davr yakunigacha saqlanadi, o'chirish kvotani tiklamaydi.
- Job idempotency metadata'si kamida 24 soat saqlanadi. Oddiy operatsion/job metadata 90 kun; prompt/rasm/matn saqlagan snapshot va natija job tugagandan 7 kun o'tib o'chiriladi. Kartochkadagi joriy kontent/oxirgi AI JSON bunga kirmaydi.
- Akkaunt o'chirishda sessiyalar darhol bekor, ishchi ma'lumotlar 24 soatda purge; shaxsiy zaxira nusxadagi qoldiq ko'pi bilan 30 kun. Foydalanuvchiga tegishli counter/job yozuvlari ham account purge'da o'chadi; global xarajat hisobida shaxsiy identifikatorsiz agregat qoladi.
- O'chirish hodisasi soft-delete bilan bir tranzaksiyada qayd etiladi; minimal reyestr backup oynasi uchun shifrlangan offsite joyga chiqariladi. Tiklash paytida eng yangi reyestr qayta qo'llanadi; uning to'liqligi tasdiqlanmaguncha tiklangan muhit public ochilmaydi. Reyestr kartochka matni yoki rasmni saqlamaydi.

## 11. Eksport shartnomasi

MVP eksporti oddiy ma'lumot eksporti. Uzum kabinetiga bevosita import qilinadi degan va'da yo'q. Rasmiy kategoriya shabloniga mos eksport alohida tasdiqlanmaguncha mahsulot nomida ham bunday da'vo bo'lmaydi.

- Bitta so'rovda 1-100 ta tayyor kartochka. Hammasi egaga tegishli, faol jobsiz va review elementlari hal qilingan bo'lishi kerak; aks holda butun so'rov xato.
- Minimal tayyorlik: ikki tilda sarlavha va tavsif, amaldagi tekshirilgan uzunlik/qoidalar, hal qilinmagan review yo'q. Material/rang kabi noma'lum ixtiyoriy qiymat bo'sh qolishi mumkin.
- Eksport saqlangan versiyalarning bitta izchil DB snapshot'idan olinadi; tartib tanlangan ID'lar tartibi. ID takrori 422.
- Ustunlar: `card_id`, `version`, `created_at_utc`, `title_uz`, `title_ru`, `short_description_uz`, `short_description_ru`, `description_uz`, `description_ru`, `suggested_category_uz`, `suggested_category_ru`, `color_uz`, `color_ru`, `material_uz`, `material_ru`, `keywords_uz`, `keywords_ru`, `attributes_json`.
- Keyword va attribute qiymatlari standart JSON serializer bilan; bo'sh ixtiyoriy qiymat bo'sh katak. Rasm, sessiya, prompt, email va provider kaliti eksport qilinmaydi.
- XLSX: `Cards` nomli bitta sheet, qat'iy ustun tartibi, wrap text, muzlatilgan birinchi qator; foydalanuvchi/AI matni formula emas, string turi bilan yoziladi.
- CSV: UTF-8 BOM, vergul delimiter, CRLF, standart CSV writer; qo'shtirnoq, vergul, o'zbek/rus harflari va ko'p qatorli matn uchun round-trip test.
- Spreadsheet formula injection: boshidagi whitespace/control belgilaridan keyin `=`, `+`, `-`, `@` kelsa xavfli qiymat matn sifatida zararsizlantiriladi. CSV uchun oldiga apostrof, XLSX uchun explicit string. Tab/CR/LF prefiksli va oddiy `=1+1` testlari majburiy.
- XLSX XML'da mumkin bo'lmagan control belgilar uchun aniq sanitizatsiya; matnni jimgina kesish yo'q. Excel katak limitiga sig'masa xato va tegishli maydon qaytariladi.
- `Content-Disposition: attachment`, belgilangan MIME, `Cache-Control: no-store`; serverda doimiy eksport fayli saqlanmaydi. Katta eksportlar uchun 100 kartochka va 10 MiB chiqish chegarasi.

## 12. Texnik tuzilma

| Qism | Tanlov |
| --- | --- |
| Runtime | Python 3.12; aniq dependency versiyalari lock qilinadi. Lokal muhit Windows, konteynerlar Linux. |
| Web | FastAPI, Uvicorn, Pydantic 2, pydantic-settings. Route -> service -> model/provider; ortiqcha repository framework'i yo'q. |
| UI | Jinja2, HTMX, kichik vanilla JS modullari; React va SPA router yo'q. |
| CSS/asset | Tailwind build, Node 24 LTS build muhiti; prod'da Tailwind CDN/play script yo'q. Shrift, HTMX va ikonlar lokal asset. |
| DB | PostgreSQL 16, SQLAlchemy 2, Alembic; psycopg. Bitta web process va alohida worker. |
| AI | Rasmiy Google Gen AI SDK adapter ortida; model/timeout/narx sozlamadan. |
| Xavfsizlik/fayl | Argon2 kutubxonasi, SlowAPI, Pillow; fayl chegarasi reverse proxy va ilovada. |
| Eksport | openpyxl va standart `csv`/`json`. |
| Email | SMTP adapteri; lokal/testda soxta xat qutisi, prod'da haqiqiy yetkazish. |
| Test/sifat | pytest, HTTPX, PostgreSQL integratsiyasi, Playwright, Ruff; AI mock bilan. |
| Deploy | Docker Compose: web, worker, PostgreSQL, Caddy; private upload volume, HTTPS. Redis, Celery va Kubernetes MVP'da yo'q. |

Amalga oshirilgan modullar: [app/config.py](app/config.py), [app/main.py](app/main.py), [app/models](app/models), [app/schemas.py](app/schemas.py), [app/routes](app/routes), [app/services](app/services), [app/ai](app/ai), [app/worker.py](app/worker.py), [app/cli.py](app/cli.py), [app/evaluation.py](app/evaluation.py), [app/templates](app/templates), [frontend](frontend), [migrations](migrations), [tests](tests), [e2e](e2e). Sifat baholash `karto eval` buyrug'i orqali.

Konfiguratsiya: `APP_ENV`, `APP_BASE_URL`, `DATABASE_URL`, `UPLOAD_DIR`, `AI_PROVIDER`, `AI_MODEL`, `GEMINI_API_KEY`, `AI_DAILY_BUDGET_USD`, model narxi/versiyasi, SMTP host/port/user/password/from, ishonchli proxy/hostlar. `.env.example` faqat bo'sh yoki xavfsiz namunalar; real kalitlar Git, chat va browser bundle'ga kirmaydi. AI mock rejimi prod'da taqiqlanadi.

## 13. Sifat va qabul mezonlari

Har bir vazifa yonidagi test shu vazifa bilan yoziladi; yakunda testlarni noldan boshlash yo'q. T52-T61 ushbu testlarni xavf bo'yicha yakuniy tekshiruvdan o'tkazadi.

| Xavf | Qabul tekshiruvi |
| --- | --- |
| Begona ma'lumot | A foydalanuvchi B ning card/image/job/export ID'sini ishlatsa 404; aralash bulk eksport ham to'liq rad etiladi. |
| Auth | Token bir martalik/eskirgan, sessiya rotation/expiry, CSRF, umumiy auth javobi, parol o'zgarganda sessiyalar bekorligi. |
| Fayl | 0/6 rasm, 10 MiB dan katta, soxta MIME/kengaytma, animatsiya, katta pixel count, traversal nom, disk xatosi. |
| Kvota | 20/40/10 chegaralari, 21-chi initial rad etilishi, failure/refund, o'chirishda qaytmasligi, Toshkent yarim tuni/oy almashishi. |
| Parallel so'rov | Oxirgi kvotaga parallel ikkita request, double-click, bir xil key/boshqa payload; ikki marta sarf yoki ikki faol job yo'q. |
| Worker | Queued/running restart, lease expiry, kech javob, DB commit uzilishi; stale job tiklanadi va rezerv bir marta qaytariladi. |
| AI | Noto'g'ri JSON, yetishmayotgan/ortiqcha maydon, timeout, 429/5xx, yolg'on ishonch, rasmdagi prompt injection; ko'pi bilan 2 chaqiruv. |
| Tahrir | Version konflikti, unsaved o'zgarishlar, regeneratsiya boshqa maydonlarga tegmasligi, oldingi matn xatoda saqlanishi. |
| Eksport | Formula, whitespace/control, Unicode, uzun/ko'p qatorli katak, 1/100/101 element, atomar egalik tekshiruvi. |
| UI | Desktop/mobil happy path, klaviatura, tab/fokus, bo'sh/xato/kutish holatlari, screenshot va overlap tekshiruvi. |
| Operatsiya | HTTPS/private storage, migratsiya, backup'dan tiklash, o'chirilgan ma'lumotni qayta oshkor qilmaslik, worker alert. |

20 namuna kamida 4 mahsulot toifasidan bo'ladi; 5 tasi noaniq/yetarli fakt yo'q holat, bir nechtasi 2-5 rasmli. Etalonni sotuvchi tasdiqlaydi. Baholovchi til sifati, faktlar, maydon to'liqligi va tahrir vaqtini qayd etadi. Qabul: kamida 17/20 yaroqli; barcha namunada tasdiqsiz muhim fakt yakuniy fakt sifatida qolmasin.

Hisobotda model/prompt/rules/schema versiyasi, token/xarajat (noma'lum bo'lsa shu holat), provider va navbat vaqti alohida bo'ladi. Real AI sinovi egasi bergan kalit va tasdiqlangan budjet bilan qo'lda; API kalitsiz testlar yakuniy AI sifatini isbotlamaydi.

Moderatsiya Uzum qarori, shuning uchun 100% o'tish kafolati berilmaydi. Bir nechta pilot kartochkani sotuvchi o'zi kabinetga qo'yib ko'rishi real tekshiruv hisoblanadi; bu uning ruxsatisiz avtomatik qilinmaydi.

## 14. Tashqi tekshiruvlar va manbalar

2026-09-28 da [Uzum Seller qo'llanmasi 5-bo'limi](https://seller.uzum.uz/manual/5.product-creation/) va Gemini narx/structured output sahifalari ochib tekshirildi. Tasdiqlangan: sarlavha, tavsif, qisqa tavsif va xususiyatlar o'zbek (lotin) va rus (kirill) tilida; sarlavha kamida 3 so'z, emoji/CapsLock/ortiqcha belgi, aloqa ma'lumoti, tashqi havola va stop-so'zlar taqiqlangan; qisqa tavsif 1-2 gap. Qo'llanmada aniq belgi limitlari va kartochka yaratish uchun Excel import shabloni topilmadi, shuning uchun ular ichki chegara va oddiy eksport sifatida qoladi. Qolgan tekshiruvlar:

| Tekshiruv | Boshlang'ich rasmiy manba | Natija va qaysi ishni cheklaydi |
| --- | --- | --- |
| Uzum kartochka qoidalari | [Uzum Seller qo'llanmasi](https://seller.uzum.uz/manual/) va sotuvchi kabineti | T02: maydon/til/uzunlik/taqiq bo'yicha qiymat, aniq bo'lim URL, tekshirish sanasi; Uzumga moslik da'vosi va pilotdan oldin kerak. |
| Ommaviy import | Seller kabinetining tegishli kategoriya bo'limi va amaldagi shablon | T03: shablon borligi va haqiqiy sinov; topilmasa MVP oddiy eksport bilan qoladi, bu core kodlashni to'xtatmaydi. |
| Uzum API imkoniyatlari | Seller kabinetidan rasmiy API hujjatiga havola | P2-04: ruxsatlar, autentifikatsiya, endpoint va limitlar; MVP uchun zarur emas. |
| AI model va JSON/rasm | [Gemini modellari](https://ai.google.dev/gemini-api/docs/models) va [structured output](https://ai.google.dev/gemini-api/docs/structured-output) | T04: tanlangan model mavjudligi va haqiqiy test chaqiruvi; provider ulashdan oldin. |
| AI xarajat va ma'lumot | [Gemini narxlari](https://ai.google.dev/gemini-api/docs/pricing) va [xizmat shartlari](https://ai.google.dev/gemini-api/terms) | T04/T66: narx versiyasi, kvota, region va rasm ma'lumoti ishlatilishi; real foydalanuvchi rasmi yuborilishidan oldin. |

Egadan kerak bo'ladigan resurslar: ishlatishga ruxsatli 20 mahsulot namunasi va faktlari; AI kaliti/xarajat budjeti; ommaviy sinovdan oldin SMTP, domen va VPS. Kalitlarning o'zi reja fayliga yoki chatga yozilmaydi. Ular yo'q bo'lsa mock orqali lokal ishlab chiqish mumkin, lekin real AI va ommaviy ishga tushirish bajarildi deb belgilanmaydi.

## 15. Bajarish bosqichlari

| Bosqich | Vazifalar | Chiqish mezoni |
| --- | --- | --- |
| M0: tekshiruv/resurs | T01-T06 | Muhit tekshiruvi, qoida va model dalili, namuna to'plami, resurs egasi aniq. |
| M1: poydevor va auth | T07-T15 | PostgreSQL migratsiyasi, sozlamalar, egaga kirish, xavfsiz akkaunt oqimi. |
| M2: kartochka va fayl | T16-T22 | Sxema, xavfsiz draft/rasm, version/tasdiq, o'chirish va atomar kvota. |
| M3: AI va navbat | T23-T35 | Rasm -> saqlangan natija; xato/restart/takroriy so'rovda kvota va matn to'g'ri. |
| M4: foydalanuvchi interfeysi | T36-T46 | Yangi kartochka, muharrir, tarix, profil va barcha holatlar desktop/mobilda. |
| M5: eksport | T47-T51 | Bir/ko'p kartochkali XLSX/CSV, xavfsiz va tekshirilgan. |
| M6: qabul sinovlari | T52-T61 | Xavf matritsasi, E2E, CI va real AI baholashining dalillari. |
| M7: chiqarish va pilot | T62-T72 | HTTPS, monitoring, tiklangan backup, privacy, pilot va chiqarish qarori. |

Bu raqamlar qat'iy ketma-ket sprint emas: TODO'dagi individual bog'liqliklar ustun. Mock UI va eksport shartnomasi tayyor bo'lgach parallel ishlanishi mumkin. Haqiqiy rasmlar/kalit talab qilinmaydigan ishlar T04/T06 ni kutib qolmaydi.

Bitta tajribali full-stack ishlab chiquvchi uchun dastlabki taxmin: 20-30 ish kuni, tashqi resurs/tekshiruv kutishidan tashqari. Bu kafolatlangan muddat emas; T34 AI baholashidan keyin qayta baholanadi. Dastlab aytilgan 2-3 hafta faqat qisqartirilgan demo uchun realistik bo'lishi mumkin, ushbu to'liq MVP majburiyatiga aylantirilmaydi.

## 16. Ishga tushirish va operatsiya

- Lokal: Python/Node/Docker Desktop+WSL2 mavjudligi tekshiriladi. Linux worker/prod muhiti Docker orqali bir xil; portlar bo'sh bo'lmasa konfiguratsiyada boshqa port.
- Staging va production alohida DB, upload volume va kalitlar; test natijasi production ma'lumotiga yozilmaydi. Boshlang'ich VPS taxmini 2 vCPU, 4 GB RAM, 40 GB disk; T70 o'lchovi asosida moslanadi.
- Public internetga faqat Caddy 80/443. DB va worker ichki tarmoqda, web proxy ortida, konteynerlar imkon qadar non-root, filesystem huquqlari minimal.
- Deploy: versiyalangan image build -> backup -> migratsiya -> web/worker -> readiness -> bitta sintetik smoke flow. Migration mos kelmasa eski image'ga shunchaki qaytish emas, alohida tiklash rejasi ishlatiladi.
- Graceful worker shutdown uchun 150 soniya; majburiy uzilish lease tiklanish mexanizmi bilan qoplanadi.
- Loglarda `request_id`, `job_id`, xato kodi, model/versiya, davomiylik va usage bor; rasm, to'liq matn, email, cookie, token va connection string yo'q. Sentry faqat scrubbing va PII o'chirilgan holda.
- Alert: worker heartbeat 60 soniyadan ko'p eskirishi, eng eski navbat 2 daqiqadan oshishi, ketma-ket 5 provider xatosi, kunlik budjet 80%/100%, disk 80%/90%. 90% disk holatida yangi upload to'xtaydi, mavjud matnni ko'rish/eksport ishlaydi.
- DB va private rasmlar kunlik bir-biriga mos backup qilinadi; bir xil backup generation manifesti, olish paytida yozishni vaqtincha to'xtatish yoki izchil snapshot strategiyasi. Backup serverdan tashqarida shifrlangan, saqlash 30 kun.
- Pilot uchun RPO 24 soat, RTO maqsadi 4 soat; alohida muhitga DB+rasmni tiklab o'lchanmaguncha bajarilgan deyilmaydi. O'chirish reyestri tiklashdan keyin qayta qo'llanadi.
- Maintenance: token/sessiya expiry, yetim/draft/deleted fayllar, eski job payloadlari, kvota invariantlari; dry-run va qayta bajarish xavfsizligi. Manual operator amali auditga yoziladi, faol rezervlar xabarsiz o'zgartirilmaydi.
- Hisob analitikasi mavjud job/usage hodisalaridan olinadi; tashqi tracking yoki marketing cookie MVP uchun kerak emas.

Pilotga kirish sharti: T01-T70 ning tegishli dalillari, shu bosqichlardagi P0/P1 mezonlari va real AI sinovi bajarilgan, muhim xavfsizlik/ma'lumot yo'qotish xatosi ochiq emas. T71 pilotdan keyin T72 yakuniy go/no-go yoziladi; MVP tayyor deyilishi uchun barcha 72 vazifaning qabul mezoni yopiladi. Hujjatning o'zi bu shartlarni bajarmaydi.

## 17. Keyingi bosqichlar

P2 ishlar MVPga yashirin qo'shilmaydi; pilot natijasi va alohida tasdiqdan keyin boshlanadi.

- Ruscha interfeys, kartochkadan nusxa olish va oddiy foydalanuvchi bahosi.
- Rasmiy import shabloni mavjud va sinovdan o'tgan bo'lsa kategoriya bo'yicha eksport adapteri.
- Uzum integratsiyasi: hujjat/ruxsatlarni qayta tekshirish, read-only ulanish, tokenni boshqariladigan kalit bilan shifrlash, bekor qilish va rate-limitli sinxronlash.
- Qoldiqdagi farq: bir xil SKU, do'kon, holat va vaqt oralig'idagi kirim/chiqim/qaytish/nuqson/tranzit hodisalarini moslashtirish. API'dagi yig'indi va joriy snapshot sonlarini ko'r-ko'rona ayirib "yo'qolgan" deb bo'lmaydi.
- Tekshiruv hisobotida yuborilgan/qabul qilingan farqi va manba hujjatlari alohida; ehtimoliy farq tasdiqlangan yo'qotish deb nomlanmaydi. Takroriy hodisa, qisman qabul, kechikkan qaytish va kesishuvchi statuslar etalon ma'lumotda sinovdan o'tadi.
- To'lov, jamoa, Telegram/Google login va mobil ilova uchun alohida talab va xavfsizlik rejalari kerak; hozir implementatsiya majburiyati yo'q.

## 18. Vazifa tayyor hisoblanishi

Vazifa faqat tavsif yozilgani uchun bajarilgan emas. Tegishli xatti-harakat ishlashi, qabul mezoni uchun dalil/test bo'lishi, yangi xato kiritilmasligi va xavfsizlik talabi qoplanishi kerak. Tashqi tekshiruvga sana va rasmiy manba; testga buyruq/natija; UI'ga desktop/mobil dalil; deploy'ga smoke va restore natijasi yoziladi.

[TODO.md](TODO.md) dagi checkbox faqat shu dalil mavjud bo'lganda belgilanadi. "Reja tayyor", "kod tayyor", "sinovdan o'tgan" va "serverda ishlayapti" alohida holatlardir.
