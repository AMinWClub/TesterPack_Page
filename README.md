# انتخاب تسترهای سراد

پروژه‌ای ساده با FastAPI و اتصال غیرهمزمان MongoDB از طریق `pymongo.AsyncMongoClient`.
Python 3.10 یا جدیدتر و یک سرور MongoDB در دسترس نیاز است.

## نصب

دستورها را در ریشهٔ پروژه اجرا کنید. برای فعال‌کردن محیط موجود در PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

در Linux/macOS فعال‌سازی با `source .venv/bin/activate` انجام می‌شود.

## تنظیم MongoDB

فایل `.env` با تنظیمات توسعهٔ محلی ایجاد شده است. برای ساخت مجدد آن:

```powershell
Copy-Item .env.example .env
```

در Linux/macOS از `cp .env.example .env` استفاده کنید.
MongoDB محلی را روی پورت `27017` اجرا کنید، یا `MONGODB_URI` را به آدرس سرور خود تغییر دهید.
برای MongoDB Atlas می‌توانید URI از نوع `mongodb+srv://...` وارد کنید.

| متغیر | کاربرد |
| --- | --- |
| `APP_NAME` | عنوان برنامه در مستندات API |
| `MONGODB_URI` | آدرس اتصال MongoDB؛ الزامی |
| `MONGODB_DB_NAME` | نام دیتابیس؛ الزامی |
| `MONGODB_TIMEOUT_MS` | مهلت انتخاب سرور و عملیات، به میلی‌ثانیه؛ پیش‌فرض `5000` |

تنظیمات از `.env` ریشهٔ پروژه خوانده می‌شوند؛ متغیرهای محیط سیستم اولویت دارند.
فایل `.env` در Git نادیده گرفته می‌شود.

## اجرا

```shell
uvicorn app.main:app --reload
```

بررسی سلامت: `GET http://127.0.0.1:8000/health`

```json
{"status": "ok"}
```

مستندات تعاملی در `http://127.0.0.1:8000/docs` در دسترس است.
هنگام راه‌اندازی، Lifespan یک کلاینت MongoDB ایجاد و با `ping` اتصال را بررسی می‌کند.
اگر اتصال برقرار نشود، راه‌اندازی ناموفق می‌شود؛ کلاینت حتی در این حالت بسته خواهد شد.
در زمان خاموش‌شدن نیز کلاینت با `await client.close()` بسته می‌شود.
مسیر `/health` زنده‌بودن برنامه را بررسی می‌کند و در هر درخواست MongoDB را دوباره بررسی نمی‌کند.
دیتابیس از `request.app.state.db` قابل دسترسی است؛ هیچ Collection کاتالوگ جدیدی ساخته نمی‌شود.

## کاتالوگ واقعی عطرها

بعد از اجرای Uvicorn، صفحه را در `http://127.0.0.1:8000/` باز کنید. فایل‌های مستقل فرانت در `/frontend/` از همان FastAPI سرو می‌شوند؛ نیازی به CORS یا سرور فرانت جدا نیست.
مسیر `/` پیش‌نمایش عمومی کاتالوگ است و امکان ثبت واقعی ندارد. مشتری باید لینک اختصاصی `/<token>` را باز کند تا اطلاعات واقعی سفارش و ثبت نهایی فعال شوند.

| مسیر | پاسخ |
| --- | --- |
| `GET /api/tester/catalog` | `products` و `total` واقعی؛ فقط مشخصات سبک کارت و جست‌وجو |
| `GET /api/tester/perfumes/{pid}` | جزئیات همان محصول مجاز؛ آکوردها، نت‌ها و خلاصهٔ شاخص‌ها |

منبع موجودی فقط `products_api` است: وجود `vol_idx`، نبود «شخصی» در نام و حداقل یک Variation با مقدار BSON Boolean برابر `true`.
نتیجه با `vol_idx` صعودی و سقف ۴۹۸ خوانده می‌شود؛ دادهٔ جایگزین یا تعداد ساختگی اضافه نمی‌شود.
هر Refresh یک Query برای محصولات و یک Query دسته‌ای `$in` برای `fragrantica_products` دارد؛ اطلاعات نت‌ها و شاخص‌ها هنگام درخواست Detail با Projection جدا خوانده می‌شود.
در Detail علاوه بر عضویت در کاتالوگ، موجودی و شرایط محصول دوباره از منبع اصلی بررسی می‌شوند؛ محصول غیرمجاز `404` می‌گیرد.
خطای MongoDB با پاسخ عمومی `503` گزارش می‌شود و اطلاعات اتصال در پاسخ یا لاگ ثبت نمی‌شود.

کش کاتالوگ ۶۰ ثانیه و مستقل برای هر Process است. یک `asyncio.Lock` از Refresh هم‌زمان تکراری جلوگیری می‌کند؛ پس از انقضا، درخواست بعدی Refresh می‌کند. JSON پاسخ هم یک بار هنگام Refresh ساخته می‌شود تا Cache Hit دوباره تمام محصولات را Serialize نکند.
کلاینت MongoDB همان کلاینت Lifespan است. Redis، Collection جدید، اتصال به‌ازای هر درخواست یا ایندکس جدید روی دادهٔ واقعی ایجاد نشده است.
پاسخ‌های بزرگ با GZip برای کلاینت‌هایی که آن را می‌پذیرند فشرده می‌شوند.

`pid` در API و فرانت همیشه رشته است؛ برای Join، رشتهٔ عددی استاندارد و Integer معادل‌اند، اما `0012` و `12` متفاوت می‌مانند.
محصول بدون Fragrantica حذف نمی‌شود؛ فیلدهای ناموجود «نامشخص» یا حالت خالی دارند.
نگاشت‌های فارسی در `app/catalog_labels.py` متمرکز هستند و فرانت ترجمهٔ مستقل و تکراری ندارد:

- جنسیت از بیشترین رأی مثبت معتبر، با پنج عنوان نمایشی و سه گروه فیلتر تعیین می‌شود؛ تساوی یا نبود رأی، نامشخص است.
- فصل‌هایی با حداقل ۶۵٪ رأیِ بیشترین فصل نمایش داده می‌شوند؛ `day` و `night` نقشی ندارند. پشتیبانی از هر چهار فصل، «چهار فصل» تولید می‌کند.
- هر محصول `seasonKeys` شامل شناسه‌های `spring`, `summer`, `autumn`, `winter` و `accordKeys` شامل کلیدهای اصلی آکوردها دارد. فصل از `seasonVotes` مستقیم یا زیر `votes`، با همان آستانهٔ ۶۵٪، استخراج می‌شود. آکورد فقط با نام متنی معتبر و Strength عددی مثبت و متناهی پذیرفته می‌شود؛ Boolean، رشتهٔ عددی و وزن صفر/منفی معتبر نیستند. کلید آکورد با NFKC، یکسان‌سازی بزرگی حروف و فاصله‌ها ساخته و موارد تکراری حذف می‌شوند. نبود داده به آرایهٔ خالی منجر می‌شود.
- پاسخ کاتالوگ همچنین یک دیکشنری `accordLabels` دارد؛ کلید آن همان Accord Key و مقدار آن ترجمهٔ Mapping موجود است. برای آکورد ناشناخته، نام اصلی حفظ می‌شود. این دیکشنری فقط از محصولات همان کاتالوگ محدودشده ساخته می‌شود و نیازی به Query اضافه ندارد.
- طبع از `temperature`، شیرینی/تازگی بالا و آکوردهای معتبر مرتب‌شده ساخته می‌شود؛ شیرینی پایین به‌تنهایی «تلخ» معنی نمی‌دهد.
- ماندگاری و پخش بو از دستهٔ دارای بیشترین رأی معتبر تعیین می‌شوند؛ نبود رأی یا تساوی، نامشخص است. رأی‌ها، تعداد و Average خام به فرانت فرستاده نمی‌شوند.
- نام نت‌های تخصصیِ بدون ترجمه با نام اصلی باقی می‌ماند. `originalName` و `noteId` برای تطبیق حفظ می‌شوند؛ قدرت آکورد واقعی تغییر نمی‌کند و رنگ فقط Hex معتبر است.
- تگ‌های توصیفی `descriptiveTags` فقط در Detail، با `id` و `label` فارسی برگردانده می‌شوند و زیر ماندگاری/پخش بو نمایش دارند. ترجمهٔ ۲۱ تگ فعلی متمرکز است؛ شناسهٔ ناشناخته با متن اصلی نمایش داده می‌شود. وزن خام منتقل نمی‌شود و در نبود تگ، این بخش پنهان است.

تصویر اصلی از فیلد معتبر `imageUrl` یا `image_url` در `products_api` و سپس `imageUrl` در `fragrantica_products` خوانده می‌شود. پس از به‌روزرسانی داده‌ها، تصویر واقعی محصول در فیلد `fragrantica_products.imageUrl` موجود است. برای تصویر ناموجود، ناامن یا خراب، یک Placeholder عمومی نمایش داده می‌شود؛ تصویر عطر حدس زده نمی‌شود.
تصاویر نت‌ها از `imageUrl` واقعی به `image` تبدیل می‌شوند. URL فقط HTTPS عمومی و بدون اعتبارنامه پذیرفته می‌شود؛ فرانت از Lazy Loading، `no-referrer` و Fallback تصویر خراب استفاده می‌کند. سرور تصویر خارجی را دانلود یا Proxy نمی‌کند.
`descriptionText`، توضیحات بلند، رأی خام، پیشنهاد عطر مشابه و دادهٔ سفارش در پاسخ‌ها قرار نمی‌گیرند.

فرانت کل کاتالوگ را یک بار می‌گیرد و متن قابل جست‌وجو را یک بار برای هر محصول آماده می‌کند.
جست‌وجو روی نام اصلی، Label، نام Fragrantica/انگلیسی، برند و مشخصات نمایشی محلی است؛ «ي/ی»، «ك/ک»، اعراب، فاصله/نیم‌فاصله و بزرگی حروف انگلیسی یکسان‌سازی می‌شوند.
ابتدا ۲۴ کارت در DOM ساخته می‌شود و اسکرول یا دکمهٔ «نمایش بیشتر» بسته‌های بعدی را اضافه می‌کند. شمارنده همیشه تعداد تمام Matchهاست.
جست‌وجو و فیلتر از ابتدای نتایج نمایش می‌دهند، اما انتخاب‌ها و شناسه‌های رشته‌ای در `Map`/حافظه باقی می‌مانند.
کنار جنسیت، فصل استفاده و انتخاب چند آکورد با جست‌وجوی فارسی/انگلیسی قرار دارند. گزینه‌های آکورد از `accordKeys` محصولات همین پاسخ ساخته می‌شوند؛ جست‌وجوی داخل منوی آکورد فقط گزینه‌های منو را محدود می‌کند. بین جنسیت، فصل، آکورد و جست‌وجوی محصول AND و بین آکوردهای انتخاب‌شده OR اعمال می‌شود. محصول فاقد داده با فیلتر فعال همان گروه Match نمی‌شود. برچسب‌های فعال، حذف جداگانه، پاک‌کردن همه، شمارنده و حالت بدون نتیجه همگی محلی‌اند؛ فیلترها درخواست API اضافه نمی‌فرستند و سبد انتخاب را تغییر نمی‌دهند.
Detail هنگام بازشدن Modal دریافت و فقط پس از موفقیت در حافظهٔ صفحه Cache می‌شود؛ Loading، خطا، Retry و پاسخ دیررس عطر قبلی مدیریت می‌شوند.
در نبود API، خطای واضح نمایش داده می‌شود و کاتالوگ Mock جایگزین نمی‌شود.

### تست‌های کاتالوگ

بدون نصب وابستگی تست اضافه، Unit و ASGI Testها:

```shell
python -m unittest tests.test_catalog -v
```

Integration واقعی MongoDB با دیتابیس موقت `tester_catalog_it_<uuid>`، دادهٔ ساختگی و پاک‌سازی همان دیتابیس اجرا می‌شود. هیچ نوشتنی روی Collectionهای واقعی انجام نمی‌شود:

```powershell
$env:TESTER_RUN_CATALOG_INTEGRATION = "1"
python -m unittest tests.test_catalog_integration -v
Remove-Item Env:TESTER_RUN_CATALOG_INTEGRATION
```

در Linux: `TESTER_RUN_CATALOG_INTEGRATION=1 python -m unittest tests.test_catalog_integration -v`.
این تست‌ها شامل فیلتر موجودی، نوع Boolean، ترتیب/سقف، Join شناسه‌ها، نبود Fragrantica، Cache هم‌زمان، API و بازیابی اتصال هستند.
تست‌های فصل، مرز دقیق ۶۵٪، هر دو ساختار رأی، آکورد تکراری/نامعتبر، ترجمه و Metadata کاتالوگ نیز پوشش داده می‌شوند. تست مرورگر ترکیب AND/OR، حفظ سبد، پاک‌کردن فیلترها و نبود اسکرول افقی در عرض‌های ۳۲۰، ۳۹۰، ۷۶۸ و ۱۴۴۰ را بررسی می‌کند.

برای بررسی فرانت با Node 22+ و مرورگر Chromium نصب‌شده، ابتدا در یک ترمینال سرور را اجرا کنید و سپس تست را در ترمینال دیگر اجرا کنید:

```shell
uvicorn app.main:app --host 127.0.0.1 --port 8787
node tests/test_catalog_frontend.cjs
```

تست در Windows به‌صورت پیش‌فرض Edge را پیدا می‌کند؛ برای مسیر دیگر `CATALOG_BROWSER_PATH` را تنظیم کنید.
آدرس سرور با `CATALOG_TEST_BASE_URL` قابل تغییر است. بررسی اولیهٔ UI فقط کاتالوگ واقعی را می‌خواند؛ خطاها و سناریوهای شناسه با APIهای Mock در مرورگر تست می‌شوند.
برای بررسی کاملاً ایزوله، به‌جای سرور اصلی `python -m tests.selection_preview --port 8789` را اجرا و در ترمینال تست `$env:CATALOG_TEST_BASE_URL='http://127.0.0.1:8789'` را تنظیم کنید. Preview فقط دیتابیس موقت خودش را می‌سازد؛ با Ctrl+C آن را به‌صورت Graceful ببندید تا پاک‌سازی شود.
گزارش و تصاویر در `test-artifacts/catalog/` ذخیره می‌شوند و وارد Git نمی‌شوند. هیچ تستی پیامک ارسال نمی‌کند.

بررسی محلی این مرحله در ۱۴ اکتبر ۲۰۲۶: ۴۹۸ محصول واجد شرایط، ۴۹۸ Match با Fragrantica و صفر مورد بدون Match.
برای هر ۴۹۸ محصول، URL معتبر تصویر اصلی موجود بود. حجم JSON حدود ۲۳۷ KiB و پاسخ GZip حدود ۳۹ KiB بود. اولین درخواست HTTP روی Loopback پس از راه‌اندازی حدود ۲۷۸ میلی‌ثانیه و میانهٔ ۲۰ Cache Hit حدود ۱۲ میلی‌ثانیه اندازه‌گیری شد؛ این اعداد Benchmark همین محیط هستند.

## انتخاب واقعی سفارش و لینک پیامک

Worker توکن قطعی و غیرترتیبی مبتنی بر HMAC را می‌سازد و لینکی مانند `https://your-domain.example/aB7xQ2mN9pR4tY6kL8` را پیامک می‌کند؛ توکن این لینک صرفاً مثال است. مقدار `TESTER_SELECTION_BASE_URL` باید فقط Origin، مانند `https://your-domain.example` باشد؛ مسیر اضافی، Query و Fragment پذیرفته نمی‌شوند و اسلش انتهایی نرمال می‌شود. مسیر `/{token}` فقط برای توکن دقیقاً ۱۸ کاراکتری Base64 URL-safe با حروف `a-z`، `A-Z`، اعداد `0-9` و دو کاراکتر `-` و `_` همان HTML ثابت را سرو می‌کند؛ CSS/JS و لوگو مسیر مطلق دارند و Refresh نیز کار می‌کند. HTML شامل اطلاعات مشتری یا توکن تعبیه‌شده نیست. مسیر قدیمی با پیشوند حذف شده است. `/health`، `/api/`، `/frontend/`، `/docs` و `/openapi.json` مستقل‌اند و مسیر توکن آن‌ها را نمی‌گیرد؛ مسیر ریشهٔ نامعتبر `404` می‌گیرد.

```dotenv
TESTER_SELECTION_BASE_URL=https://your-domain.example
```

API توکن را دوباره تولید نمی‌کند: فرمت `[A-Za-z0-9_-]{18}` بررسی، SHA256 رشتهٔ کامل و دقیق محاسبه و Selection با `token_hash` پیدا می‌شود. توکن همیشه String و Case-Sensitive است؛ هیچ‌جا Lowercase، Uppercase، Trim یا تبدیل عددی روی آن انجام نمی‌شود. تغییر حتی یک حرف از کوچک به بزرگ Hash متفاوتی تولید می‌کند و لینک سفارش اصلی را باز نمی‌کند؛ Lookup دیتابیس هم Collation از نوع `simple` دارد. عدد JSON، ارقام فارسی/عربی، `+`، `/`، `=`، فاصله و طول متفاوت رد می‌شوند. ارتباط سفارش فقط با `order_id` از نوع اصلی `ObjectId` است. `id` مانند `OR0000087510` فقط برای نمایش استفاده می‌شود. نسخه و وضعیت Selection و وجود سفارش بررسی می‌شوند. تنها شرط ورود، `state` دقیقاً برابر رشتهٔ `pending` است؛ جنسیت، تاریخ خرید، محصول و روش ارسال محدودیتی ایجاد نمی‌کنند. API فقط Selection موجود را از Hash پیدا می‌کند و Selection جدید نمی‌سازد. خواندن رسید موفق یا وضعیت منقضی پس از تغییر state هم مجاز است؛ نوشتن انتخاب جدید فقط در state=pending و پیش از Deadline مجاز است. امنیت، Notification و Retry پیامک حفظ شده‌اند.

### قرارداد Context و Submit

توکن فقط در Body JSON است؛ آن را در Query یا URL API نفرستید.

```http
POST /api/tester/selection/context
Content-Type: application/json

{"token":"aB7xQ2mN9pR4tY6kL8"}
```

پاسخ موفق:

```json
{
  "order": {
    "id": "OR0000087510",
    "customer_name": "احمد فرجی",
    "phone": "09123456789",
    "tester_count": 10
  },
  "selection": {
    "status": "pending",
    "selected_pids": [],
    "selected_names": [],
    "availability": "active",
    "code": null,
    "expires_at": "2026-10-17T10:30:00+00:00",
    "remaining_seconds": 1799.5,
    "requires_support": false
  }
}
```

نام و نام خانوادگی Trim و با یک فاصله به هم متصل می‌شوند. موبایل کامل، با نرمال‌سازی فعلی شماره، در UI با جهت LTR نمایش داده می‌شود. آدرس، پرداخت، Tracking، ObjectId، Hash و اطلاعات داخلی برگردانده نمی‌شوند.

```http
POST /api/tester/selection/submit
Content-Type: application/json

{"token":"aB7xQ2mN9pR4tY6kL8","pids":["1001","1002","1003","1004","1005","1006","1007","1008","1009","1010"]}
```

PIDهای بالا نمونه‌اند؛ Body فقط `token` و ۱۰ PID رشته‌ایِ متمایز دارد. نامی از فرانت پذیرفته نمی‌شود. قبل از پذیرش Intent، یک Query تازه روی `products_api` با همان فیلتر، ترتیب `vol_idx` و سقف ۴۹۸ اجرا می‌شود؛ کش کاتالوگ مبنای ثبت نیست. محصول ناموجود، خارج از ۴۹۸ عضو فعلی، PID مبهم یا نام خالی/غیررشته‌ای باعث رد کل ثبت می‌شود.

`recommended_products` دقیقاً از ۱۰ مقدار اصلی `products_api.name` به ترتیب PIDهای ارسال‌شده ساخته می‌شود. فاصله‌ها، متن انگلیسی و نام کامل منبع حفظ می‌شوند؛ نام Fragrantica، ترجمه و خلاصه‌سازی جایگزین آن نمی‌شوند. پاسخ Submit موفق همان قرارداد Context با `status: submitted`، `selected_pids` و `selected_names` ده‌عضوی است.

| HTTP | رفتار |
| --- | --- |
| `200` | Context معتبر، ثبت موفق یا بازیابی همان ثبت |
| `400` | فرمت توکن یا JSON/کلیدهای درخواست نامعتبر |
| `404` | Selection/سفارش ناموجود یا لینک نامعتبر |
| `409` | انتخاب متفاوت پس از Intent، فیلد قبلی سفارش، محصول ناموجود، داده ناسازگار یا `order_not_pending` برای تلاش ثبت جدید خارج از pending |
| `409` | `sms_not_accepted` برای نبود پذیرش معتبر پیامک؛ `selection_review_required` برای ثبت نیمه‌تمام نیازمند پشتیبانی |
| `410` | `selection_expired`؛ مهلت ثبت جدید تمام شده است |
| `413` | Body بزرگ‌تر از ۸ KiB |
| `422` | تعداد نامعتبر، PID تکراری یا PID با نوع/فرمت نامعتبر |
| `429` | درخواست‌های بیش از حد؛ همراه `Retry-After` |
| `503` | خطای موقت MongoDB یا تنظیمات ناقص سرویس |

خطا به شکل `{"detail":{"code":"...","message":"پیام فارسی"}}` است و دادهٔ مشتری، ورودی توکن یا خطای خام دیتابیس را شامل نمی‌شود. موجودبودن `recommended_products` حتی با مقدار `null`، آرایهٔ خالی یا نام‌های یکسان مانع بازنویسی است. سفارش دارای فیلد قدیمی بدون شناسهٔ ثبت این سیستم، خطای کنترل‌شدهٔ `already_selected` می‌گیرد و خودکار مهاجرت یا تعمیر نمی‌شود.

### ثبت اتمیک، Idempotency و Recovery

وضعیت‌های زیر در همان `tester_selections` استفاده می‌شوند:

| وضعیت | معنی |
| --- | --- |
| `pending` | هنوز Intent ثبت نشده؛ انتخاب فقط پس از پذیرش پیامک و پیش از Deadline مجاز است |
| `submitting` | یک Intent معتبر و ثابت ثبت شده؛ نوشتن سفارش فقط پیش از Deadline مجاز است |
| `submitted` | هر دو سند سازگارند و انتخاب‌ها برای همیشه فقط‌خواندنی‌اند |
| `review_required` | ثبت نیمه‌تمامی که پس از Deadline نمی‌توان سفارش آن را نوشت، یا زمان نوشتن آن قابل اثبات نیست؛ برای بررسی پشتیبانی حفظ می‌شود |

بعد از اعتبارسنجی تازه، عملیات شرطی اتمیک `pending → submitting` با شرط `$$NOW < expires_at` فقط به یک درخواست اجازهٔ ثبت Intent می‌دهد. Intent شامل `submission_id`، ۱۰ `selected_pids`، ۱۰ `selected_names`، زمان‌ها و snapshot معتبر `sms_accepted_at` و `expires_at` است؛ درخواست دیگر نمی‌تواند آن را جایگزین یا تمدید کند. ساخت Intent به‌تنهایی ثبت نهایی نیست؛ موجودی رزرو یا کم نمی‌شود.

سپس سفارش فقط با `_id` واقعی، شرط دقیق `state=pending`، شرط `recommended_products: {$exists: false}`، نبود Marker و بررسی `$$NOW < expires_at` نوشته می‌شود. علاوه بر Predicate اولیهٔ Query، یک Update Pipeline شرط مهلت و state را در خود `$cond` دوباره بررسی می‌کند؛ فقط در شاخهٔ مجاز، `$mergeObjects` هر سه فیلد را یکجا اضافه می‌کند و Timestamp از همان `$$NOW` ثابت Pipeline گرفته می‌شود. در لحظهٔ مساوی Deadline یا بعد از آن، خروجی `$$ROOT` است و کل سند بدون تغییر می‌ماند. نام‌ها و شناسهٔ ثبت با `$literal` درج می‌شوند. سه فیلد لازم:

```text
recommended_products: [10 exact source names]
tester_selection_submission_id: <same submission_id>
tester_selection_written_at: UTC BSON Date (same pipeline $$NOW)
```

`matched_count=1` ممکن است فقط به معنی اجرای شاخهٔ بدون تغییر باشد. نتیجه با خواندن دوبارهٔ سفارش و تطبیق نام‌ها، Marker و Timestamp معتبر بررسی می‌شود؛ شمارنده یا ACK به‌تنهایی موفقیت محسوب نمی‌شود. تصمیم شرطی Pipeline و Timestamp یک مبنای زمانی دارند و به یکسان‌بودن زمان ارزیابی Query اولیه و Update تکیه نمی‌کنند.

تضمین زمانی بر مبنای `$$NOW` عملیات MongoDB است که در طول Pipeline ثابت می‌ماند. این Timestamp، زمان ACK شبکه یا پایان فیزیکی Journal/Commit نیست؛ MongoDB Standalone تضمین نمی‌کند ACK یا Commit فیزیکی نیز حتماً پیش از Deadline تمام شود. تأخیر اجرای عملیات یا شبکه می‌تواند مشاهدهٔ نتیجه را عقب بیندازد. ثبت جدید فقط با زمان ارزیابی همان عملیات، کوچک‌تر از Deadline مجاز است و هر سه فیلد همان تصمیم اتمیک را دنبال می‌کنند؛ تعیین مهلت سخت بر اساس لحظهٔ فیزیکی Commit با این API قابل تضمین نیست. [متغیر `NOW`](https://www.mongodb.com/docs/manual/reference/aggregation-variables/)، [Update Pipeline](https://www.mongodb.com/docs/manual/tutorial/update-documents-with-aggregation-pipeline/)

Marker برای تشخیص ثبت همین Intent از آرایهٔ قدیمیِ احتمالا یکسان لازم است. زمان نوشتن سفارش برای اثبات ثبت پیش از Deadline و Recovery بعد از انقضا لازم است. پس از تأیید تطابق Marker، تمام نام‌ها و زمان معتبر نوشتن، Selection به `submitted` تغییر می‌کند. اطلاعات نهایی:

```text
order_id: ObjectId (unchanged)
token_hash / token_version / created_at: unchanged
status: submitted
submission_id: unique intent identifier
selected_pids: [10 strings in customer order]
selected_names: [10 exact products_api.name strings]
submitting_at / submitted_at / updated_at: UTC datetime
sms_accepted_at / expires_at: trusted UTC deadline snapshot
```

اگر قبل از نوشتن سفارش Crash رخ دهد، Context یا Submit فقط تا پیش از Deadline همان Intent را ادامه می‌دهد. پس از انقضا، سفارش خالی باقی می‌ماند و Selection با `review_required` و `review_reason=deadline_before_order_write` برای پشتیبانی نگهداری می‌شود. Intent و اطلاعات Audit پاک یا جایگزین نمی‌شوند. اگر نوشتن سفارش پیش از Deadline موفق شده ولی پاسخ یا نهایی‌کردن Selection از دست رفته باشد، Marker، نام‌ها و `tester_selection_written_at` از دیتابیس بررسی می‌شوند و حتی پس از انقضا فقط Selection تکمیل می‌شود؛ سفارش دوباره نوشته نمی‌شود. یک نوشتن به‌موقع که هم‌زمان با ثبت وضعیت پشتیبانی کامل شده باشد نیز با همین شواهد قابل نهایی‌شدن است. زمان‌های ناموجود/نامعتبر در رکورد نیمه‌تمام، به‌جای حدس موفقیت، `review_reason=unverified_order_write_time` می‌گیرند. رسیدهای از قبل `submitted`، از جمله رسیدهای پیش از این تغییر که فیلد زمان جدید ندارند، حفظ می‌شوند. هیچ Migration اجرا نمی‌شود.

Intent متفاوت `409` می‌گیرد. Retry همان ۱۰ PID با همان ترتیب پس از ثبت موفق، حتی بعد از انقضا، همان رسید قبلی را بازمی‌گرداند. Intent و سفارش ناسازگار خودکار بازنویسی نمی‌شوند.

برای مشاهدهٔ موارد نیازمند پشتیبانی، در دیتابیس تنظیم‌شده فقط رکوردهای `tester_selections` با `{status: "review_required"}` را بررسی کنید. `review_reason` علت غیرحساس را نگه می‌دارد؛ زمان شروع Intent ناموجود یا خارج از مهلت نیز `unverified_intent_time` می‌گیرد و اجازهٔ نوشتن سفارش ندارد. این وضعیت به‌صورت خودکار به `pending` برنمی‌گردد و مهلت تمدید نمی‌شود.

این روش به Transaction وابسته نیست و روی Standalone نیز کار می‌کند. Write Concern همان `majority` فعلی Worker است. ایندکس‌های یکتای `order_id` و `token_hash` فعلی حفظ می‌شوند؛ Collection، Worker، Queue یا Migration جدیدی وجود ندارد. `ensure_selection` همچنان فقط `$setOnInsert` دارد و Selection ثبت‌شده را Reset نمی‌کند.

فرانت پیش از ثبت، فهرست ۱۰ نام را برای مرور نشان می‌دهد. هنگام ارسال، تغییر انتخاب‌ها و کلیک مجدد قفل می‌شوند. در خطای شبکه موفقیت حدس زده نمی‌شود؛ Context وضعیت واقعی را بررسی/بازیابی می‌کند. اگر آن هم در دسترس نبود، وضعیت نامشخص باقی می‌ماند و Retry فقط همان PIDهای ثابت را می‌فرستد. پس از موفقیت یا بازکردن دوباره لینک، رسید از نام‌های ذخیره‌شده نمایش داده می‌شود، حتی اگر محصول بعداً ناموجود باشد؛ کاتالوگ زنده برای نمایش رسید لازم نیست.

### مهلت قابل تنظیم لینک

تنها مبنای مهلت، `accepted_at` از Notification همان سفارش با `status=accepted` است: `expires_at = accepted_at + TESTER_SELECTION_WINDOW_MINUTES`. ساخت Selection، تاریخ سفارش، بازکردن لینک و Retry ناموفق مهلت را شروع یا تمدید نمی‌کنند. Worker پس از پذیرش موفق دوباره ارسال نمی‌کند؛ `accepted_at` قبلی ثابت می‌ماند. `TESTER_SELECTION_WINDOW_MINUTES` در `.env` خوانده می‌شود؛ پیش‌فرض `30` است و فقط عدد صحیح مثبتِ قابل نمایش در datetime پذیرفته می‌شود. Context، Submit، Recovery و Release از همان تنظیم و تابع مشترک `selection_deadline` استفاده می‌کنند. مقدار خالی، صفر، منفی، اعشاری، Boolean و ارقام غیر ASCII رد می‌شوند.

`expires_at` تولیدشده توسط سرور پس از تخصیص Intent منجمد است. افزایش یا کاهش تنظیمات این تاریخ را تغییر نمی‌دهد: افزایش تنظیمات Intent منقضی را فعال نمی‌کند و کاهش تنظیمات، ثبت به‌موقع قبلی را برای Recovery و آزادسازی نامعتبر نمی‌کند. مقدار جدید فقط برای سفارش‌های بدون Deadline منجمد مبنا است. API را پس از تغییر `.env` Restart کنید و Workerهای بعدی را با همان Environment اجرا کنید.

برای تغییر مهلت در استقرار، Cron را موقتاً متوقف کنید، درخواست‌های در حال اجرای نسخهٔ قبلی API را تمام و API را با تنظیمات تازه Restart کنید؛ سپس Worker را با همان Environment از سر بگیرید تا دو نسخه با تنظیمات متفاوت هم‌زمان تصمیم نگیرند.

Context زمان فعلی را از MongoDB با `$$NOW` می‌گیرد و همهٔ زمان‌ها در Backend به UTC تبدیل می‌شوند؛ BSON Date بدون Timezone نیز UTC محسوب می‌شود. نبود Notification پذیرفته‌شده، نبود/نوع نامعتبر `accepted_at` یا زمان پذیرش در آینده، وضعیت مجزای `awaiting_sms` می‌دهد و ثبت را فعال نمی‌کند. API زمان انقضا را از Body مشتری نمی‌پذیرد. Predicate اتمیک هر دو نوشتن، ساعت MongoDB را دوباره بررسی می‌کند؛ عبور از Deadline هنگام خواندن محصولات یا بین Intent و نوشتن سفارش، اجازهٔ ثبت جدید نمی‌دهد. دقیقاً در لحظهٔ Deadline نیز شرط `<` برقرار نیست. [ساعت `NOW` در MongoDB](https://www.mongodb.com/docs/manual/reference/aggregation-variables/)

Context برای لینک معتبرِ منقضی همچنان `200` می‌دهد. `selection.status` وضعیت ذخیره‌شده است؛ `selection.availability` یکی از `active`، `awaiting_sms`، `expired`، `review_required` یا `submitted` است. اولویت ابتدا رسید `submitted` و سپس `review_required` است: مورد نیازمند پشتیبانی حتی پس از انقضا یا نبود اطلاعات زمانی معتبر، `availability=review_required` و `code=selection_review_required` دارد و وعدهٔ پک پیش‌فرض نمایش نمی‌دهد؛ Submit حل‌نشدهٔ آن `409 selection_review_required` می‌گیرد و سفارش را تغییر نمی‌دهد. برای `pending` منقضی، `code=selection_expired` و `remaining_seconds=0` انقضا را مشخص می‌کنند و Submit جدید `410` می‌گیرد. نبود پذیرش معتبر در حالت عادی `awaiting_sms` و `expires_at=null` می‌دهد. `requires_support` رکورد نیمه‌تمام را مشخص می‌کند. یک رسید موفق قبلی همچنان `200` دارد.

مسیر `/<token>` بعد از انقضا حذف یا `404` نمی‌شود. فرانت به‌جای کاتالوگ و پنل انتخاب، پیام پایان مهلت و ارسال پک پیش‌فرض متناسب با جنسیت سفارش را نشان می‌دهد. تایمر محلی از `remaining_seconds` سرور، کسر زمان رفت‌وبرگشت و `performance.now()` استفاده می‌کند؛ تغییر `Date.now()` یا ساعت سیستم مهلت را تمدید نمی‌کند. صفحهٔ باز، Drawer و Modal تأیید نیز در موعد قفل/بسته می‌شوند و هنگام بازگشت به تب، Context دوباره بررسی می‌شود. مرجع نهایی ثبت همیشه Backend است.

هیچ محصول پیش‌فرض، انتخاب محلی یا آرایهٔ ناقصی پس از انقضا در `recommended_products` نوشته نمی‌شود. تخصیص واقعی پک پیش‌فرض خارج از این تغییر است.

### امنیت و استقرار

- لینک نقش Bearer Credential دارد و صاحب لینک شمارهٔ موبایل کامل سفارش را می‌بیند؛ آن را محرمانه نگه دارید. Secret فعلی Worker را تغییر ندهید و API به تولید مجدد توکن وابسته نیست.
- در Production، TLS/HTTPS را در Reverse Proxy فعال کنید. مسیرهای `/<token>`، `/api/`، `/frontend/` و `/health` را با همان Origin به FastAPI برسانید. درخواست مستقیم `/<token>` باید به برنامه برسد؛ صرفاً فایل‌های فرانت را روی یک هاست جدا کپی نکنید.
- صفحات و APIهای اختصاصی `Cache-Control: no-store`، `Referrer-Policy: no-referrer` و `X-Robots-Tag` دارند. Metaهای متناظر و CSP محدود به اسکریپت‌های خود پروژه نیز فعال‌اند. اطلاعات مشتری وارد کش کاتالوگ یا Storage مرورگر نمی‌شود.
- توکن خام در لاگ برنامه نیست و Access Log استاندارد Uvicorn مسیر `/[redacted]` را ثبت می‌کند؛ Query، اسلش انتهایی و کاراکترهای URL-encoded نیز پوشش داده می‌شوند. مسیرهای ثابت در لاگ حفظ می‌شوند. در Proxy/CDN/APM هم ثبت URL اختصاصی و Body این APIها را غیرفعال کنید؛ Analytics و Session Replay روی صفحهٔ مشتری نصب نکنید.
- برای Nginx، `access_log off;` را در Location با Regex توکن ریشه بگذارید، کش مسیرهای اختصاصی و ثبت Body را خاموش کنید و `Referrer-Policy` را Override نکنید. HSTS را روی دامنهٔ HTTPS تنظیم کنید. لاگ‌های دیگر مانند Error Log، WAF و سرویس مانیتورینگ نیز نباید مسیر دارای توکن را ثبت کنند. نمونهٔ Location را کنار مسیرهای ثابت فعلی قرار دهید؛ Regex به آن‌ها برخورد نمی‌کند:

```nginx
location ~ "^/[A-Za-z0-9_-]{18}/?$" {
    access_log off;
    proxy_cache off;
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```
Rate Limit ساده و محدود در حافظه، پیش‌فرض ۶۰ Context/Submit در دقیقه برای هر IP و هر Process است. سقف با `TESTER_SELECTION_RATE_LIMIT_PER_MINUTE` از ۱ تا ۱۰۰۰ تنظیم می‌شود؛ Body حداکثر ۸ KiB است. این محدودسازی توزیع‌شده نیست. پشت Proxy فقط آدرس Proxyهای مورداعتماد را در `--forwarded-allow-ips` مجاز کنید تا IP مشتری درست و غیرقابل جعل باشد.

تنظیمات جدید فقط `TESTER_SELECTION_RATE_LIMIT_PER_MINUTE=60` است. `MONGODB_*` و `TESTER_ORDERS_COLLECTION` موجود برای API نیز استفاده می‌شوند. `TESTER_SMS_START_AT` حذف شده و مقدار باقی‌ماندهٔ آن در `.env` قدیمی خوانده یا اعتبارسنجی نمی‌شود. برای نصب صرفاً کاتالوگ بدون تنظیمات معتبر Worker، برنامه بالا می‌آید ولی Context/Submit با `503` غیرفعال‌اند. پیکربندی فراز و `TESTER_SMS_ENABLED` فقط ارسال Worker را کنترل می‌کنند.

پس از تنظیم `.env` و ایندکس‌های موجود با `python -m app.workers.tester_sms --create-indexes`، وب‌سرور Production را پشت HTTPS اجرا کنید؛ `--reload` فقط برای توسعه است:

```shell
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### تست فلو بدون پیامک واقعی

Unit/ASGI و Regression توکن Worker، شامل قالب Base64 URL-safe، بردار ثابت HMAC، حساسیت به حروف، نمونهٔ یکتایی ۱۰هزار سفارش، اعتبارسنجی و Retry:

```shell
python -m unittest tests.test_selections -v
python -m unittest discover -s tests -v
```

Integration واقعی در دیتابیس موقت `tester_selection_it_<uuid>`؛ تمام سفارش‌ها و محصولات ساختگی و SMSها Mock هستند:

```powershell
$env:TESTER_RUN_SELECTION_INTEGRATION = "1"
python -m unittest tests.test_selections_integration -v
Remove-Item Env:TESTER_RUN_SELECTION_INTEGRATION
```

در Linux: `TESTER_RUN_SELECTION_INTEGRATION=1 python -m unittest tests.test_selections_integration -v`.
این تست‌ها شامل Worker تا ثبت نهایی، موجودی تازه، سقف ۴۹۸، ثبت هم‌زمان، عدم بازنویسی، قطع کلاینت واقعی MongoDB، Crash و بازیابی هر دو سند هستند. تست‌های انقضا، مرز دقیق ۲۹:۵۹/۳۰:۰۰، نبود پذیرش پیامک، درخواست مستقیم و هم‌زمان، انقضا بین خواندن کاتالوگ/Intent/نوشتن سفارش، رسید بعد از انقضا و ثابت‌ماندن مهلت در اجرای دوباره Worker را پوشش می‌دهند؛ Predicateهای اتمیک و Recovery روی MongoDB واقعی نیز اجرا می‌شوند. روی سفارش‌های واقعی هیچ تست ثبت انجام ندهید.

برای تست دستی و مرورگر، سرور کاملاً ایزولهٔ زیر را به جای وب‌سرور اصلی اجرا کنید:

```shell
python -m tests.selection_preview --port 8789
```

این ابزار فقط به `127.0.0.1` گوش می‌دهد، دیتابیس `tester_selection_preview_<uuid>` و سفارش‌های آزمایشی می‌سازد و هیچ SMS ارسال نمی‌کند. توکن واقعی سناریوی `success` عمداً حروف کوچک/بزرگ و هر دو علامت `-` و `_` دارد تا حفظ دقیق آن در مرورگر و Hash آزموده شود. لینک‌های آزمایشی در `test-artifacts/selection-preview.json` هستند؛ یکی از `fixtures.*.path` را روی `http://127.0.0.1:8789` باز کنید. پس از تست، با خاموش‌شدن عادی سرور همان دیتابیس پاک می‌شود. اگر فرایند را اجباری Kill کردید، فقط دیتابیس موقت با نام دقیق ثبت‌شده در این فایل را پس از توقف سرور پاک کنید.

در ترمینال دوم با Node 22+ و Chromium/Edge نصب‌شده:

```shell
node tests/test_selection_frontend.cjs
```

هر اجرای کامل تست مرورگر به Preview تازه نیاز دارد. تست شامل سفارش واقعی در دیتابیس ایزوله، مرور/ثبت ۱۰ انتخاب، شمارهٔ کامل، Modal، دوبارکلیک، بازگشایی فقط‌خواندنی، پاسخ موفق گم‌شده، شبکهٔ نامشخص، محصول ناموجود، لینک نامعتبر، امنیت Header/Referer و عرض‌های ۳۲۰/۳۹۰/۷۶۸/۱۴۴۰ است. تصاویر و گزارش در `test-artifacts/selection/` ذخیره می‌شوند. مسیر مرورگر با `CATALOG_BROWSER_PATH` قابل تنظیم است.

## سرویس پیامک ساده فرازSMS

سرویس `send_simple` از [API رسمی Simple SMS فراز](https://docs.iranpayamak.com/send-simple-sms-13909967e0)، مسیر `POST /ws/v1/sms/simple`، استفاده می‌کند.
این متغیرها را به `.env` موجود اضافه کنید؛ تنظیمات MongoDB خود را حفظ کنید:

```dotenv
FARAZ_API_KEY=your-api-key
FARAZ_API_URL=https://api.iranpayamak.com
FARAZ_LINE_NUMBER=your-sender-line
FARAZ_TIMEOUT_SECONDS=15
```

تنظیمات با همان `python-dotenv`، از `.env` ریشه و با اولویت متغیرهای محیط خوانده می‌شوند.
تنظیمات فراز برای راه‌اندازی FastAPI الزامی نیستند؛ ساخت Payload به شمارهٔ خط و ارسال به کلید API نیز نیاز دارد.
متغیرهای قدیمی `FARAZ_PATTERN_CODE` و `TESTER_PATTERN_LINK_ATTRIBUTE` دیگر خوانده نمی‌شوند و می‌توانید آن‌ها را از `.env` حذف کنید.
نمونهٔ ساخت Payload بدون ارسال:

```python
from app.services.sms import FarazSmsClient
from app.workers.tester_sms import WorkerSettings, render_sms

client = FarazSmsClient()
settings = WorkerSettings.from_environment()
text = render_sms(settings, {"first_name": " احمد ", "last_name": " فرجی "},
                  "https://domain.com/aB7xQ2mN9pR4tY6kL8")
payload = client.build_simple_payload(recipient="09120000000", text=text)

# برای ارسال واقعی در کد مصرف‌کننده:
# result = client.send_simple(recipient="09120000000", text=text)
# result.status_code, result.data
```

هر فراخوانی فقط یک `recipient` رشته‌ای می‌پذیرد؛ در Payload به آرایهٔ تک‌عضوی `recipients` تبدیل می‌شود. شماره‌های `09...`، `989...`، `+989...` و ارقام فارسی نرمال می‌شوند.
Payload شامل `text`، `line_number`، `recipients`، `number_format="english"` و `schedule=null` برای ارسال بدون زمان‌بندی است؛ `code` و `attributes` ندارد.
متن باید String غیرخالی و موبایل معتبر باشد؛ متن بدون Trim یا بازنویسی در UTF-8 ارسال می‌شود.

متن Worker فقط از `TESTER_SMS_TEMPLATE` در `.env` خوانده می‌شود؛ متن پیامک در کد Production Hardcode نشده است:

```dotenv
TESTER_SELECTION_WINDOW_MINUTES=30
TESTER_SMS_TEMPLATE="راستی اینو یادم رفتتتت\n\n{full_name} عزیز، اون عطرهایی که می‌گفتی «باید امتحان کنم» کدوما بودن؟\n\nتسترهای دلخواهت رو تا نیم ساعت دیگه اینجا انتخاب کن که همراه سفارشت برات بفرستیم:\n\n{link}\n\nعطر سراد"
```

`\n` هم در dotenv و هم در متغیر محیط به Line Break واقعی تبدیل می‌شود؛ متن فارسی UTF-8 و فاصله‌ها حفظ می‌شوند. وجود هر دو Placeholder یعنی `{full_name}` و `{link}` الزامی است؛ Placeholder دیگری مجاز نیست. آکولاد Escape‌شده، مانند `{{full_name}}`، جایگزین Placeholder واقعی محسوب نمی‌شود. نام ناشناخته، دسترسی به Attribute/Index، Placeholder موقعیتی، Conversion، Format Specifier یا آکولاد ناقص خطای واضح `TESTER_SMS_TEMPLATE` می‌دهد؛ متن قالب در خطا چاپ نمی‌شود. اعتبارسنجی قالب قبل از Claim مرحلهٔ SMS است و سفارش را `failed` دائمی نمی‌کند. مثل سایر تنظیمات ارسال، خرابی قالب مانع اجرای مستقل Release یا API بدون SMS نیست.

Worker و API هر دو تابع مشترک `sms_full_name` را برای نام مشتری به‌کار می‌برند. نام و نام خانوادگی Trim و با یک فاصله متصل می‌شوند. بخش غیررشته‌ای، خالی، فاقد حرف، دارای کاراکتر کنترلی یا مقادیری مانند `None`/`null` نادیده گرفته می‌شود؛ یک بخش معتبر به‌تنهایی استفاده می‌شود و در نبود هر دو، `مشتری` جایگزین است. نام ناقص/نامعتبر به‌تنهایی Context، Submit یا مشاهدهٔ رسید را با `409` مسدود نمی‌کند؛ اعتبارسنجی موبایل، شناسهٔ نمایشی سفارش، توکن و وضعیت سفارش حفظ شده‌اند. نبود نام شرط Eligible بودن نیست. متن نهایی قبل از ارسال از `build_simple_payload` فراز عبور می‌کند.

لینک با `ensure_selection` و همان توکن ۱۸ کاراکتری Base64 URL-safe و Case-Sensitive ساخته می‌شود. `TESTER_SELECTION_BASE_URL=https://domain.com` لینک `https://domain.com/<token>` می‌دهد. با ثابت‌ماندن نام و تنظیمات، Retry دقیقاً همان متن شخصی‌سازی‌شده و لینک را می‌فرستد. طبق نیاز مرحلهٔ توسعه، Snapshot نام یا متن اضافه نشده است؛ تغییر نام/قالب بین تلاش‌ها در ارسال بعدی اعمال می‌شود. متن، نام شخصی‌سازی‌شده، موبایل، لینک و توکن خام در Notification/Selection یا Log ذخیره نمی‌شوند.

اگر مهلت را تغییر دادید، عبارت ثابت «نیم ساعت» در قالب نمونه را هم متناسب ویرایش کنید؛ Placeholder زمان جدیدی تعریف نشده است.

متد `send_simple` همزمان است؛ در کد `async` از `await run_in_threadpool(client.send_simple, ...)`
با import از `starlette.concurrency` استفاده کنید. خطاهای سرویس از نوع `SmsServiceError` هستند.
سرویس هیچ متن، شماره، کلید، توکن لینک یا پاسخ خامی را لاگ نمی‌کند؛ `result.body` و `error.body` را نیز در کد مصرف‌کننده لاگ نکنید.
هر فراخوانی کلاینت فقط یک درخواست ارسال می‌کند؛ Retry توسط Worker مدیریت می‌شود.
HTTP موفق به‌تنهایی کافی نیست. طبق قرارداد پروژه، پاسخ `2xx` با `status="success"` و Object در `data` تنها وقتی پذیرفته می‌شود که `data.id` عدد صحیح مثبت (غیر Boolean)، `data.type="simple"` و `data.status="pending-approval"` باشد. این وضعیت به معنی پذیرش درخواست توسط فراز است، نه تضمین تحویل به گوشی؛ استعلام تحویل انجام نمی‌شود. سایر وضعیت‌های ناشناخته موفق فرض نمی‌شوند و رفتار Retry فعلی را حفظ می‌کنند. پاسخ قدیمی با `data` عددی متناهی نیز با همان رفتار قبلی پشتیبانی می‌شود.
در پاسخ Object-based، `result.data` فقط شناسهٔ عددی `data.id` است؛ برای پاسخ عددی قدیمی همان مقدار قبلی برمی‌گردد. Worker فقط همین مقدار را در `provider_data` همراه وضعیت و HTTP Status ذخیره می‌کند؛ بدنهٔ کامل و `metadata` ذخیره یا لاگ نمی‌شوند. بدنه همچنان در `result.body` در حافظه در دسترس است و نباید لاگ شود؛ از `repr(result)` نیز حذف شده است. ویژگی `request_id` وجود ندارد.
پذیرش معتبر، Notification را به `accepted` و سفارش را به `sms_sent=true` می‌رساند. `accepted_at` زمان UTC تأیید پذیرش توسط سیستم است؛ مقدار `schedule` پاسخ فراز هیچ نقشی در آن ندارد. مهلت انتخاب با `TESTER_SELECTION_WINDOW_MINUTES`، پیش‌فرض ۳۰ دقیقه، از همین زمان محاسبه می‌شود. اجرای بعدی Worker پیامک پذیرفته‌شده را تکرار نمی‌کند؛ اگر همگام‌سازی سفارش شکست خورده باشد، فقط Repair فعلی انجام می‌شود و زمان پذیرش تمدید نمی‌شود.
خطاهای HTTP، اتصال، `status="error"` و پاسخ نامعتبر با `SmsProviderError` گزارش می‌شوند.

اجرای تست‌های Payload، تنظیمات و پاسخ Mock؛ تمام درخواست‌های HTTP در تست‌ها مسدود یا Mock می‌شوند:

```shell
python -m unittest tests.test_sms_faraz tests.test_tester_sms_worker -v
```

## Worker سفارش‌های پک تستر

Worker یک‌نوبتی، در همان اجرا ابتدا آزادسازی سفارش‌های تعیین‌تکلیف‌شده، سپس Recovery پیامک و در پایان ارسال‌های جدید/Retry را انجام می‌دهد و خارج می‌شود. Cron یا Worker دیگری لازم نیست.
تنها شرط کسب‌وکاری ورود سفارش به فرایند، `state` دقیقاً برابر رشتهٔ `pending` است:

```javascript
{ state: "pending" }
```

برای جلوگیری از تطبیق آرایه‌ای مثل `["pending"]`، Query علاوه بر برابری state، با `$expr` نوع String را بررسی می‌کند؛ Collation همهٔ بررسی‌ها `simple` است. مقدار `Pending`، `pending `، مقدار غیررشته‌ای یا نبود state پذیرفته نمی‌شود.

`tester_gender` با هر مقدار یا نوع، نبود/خالی‌بودن آن، `date_created` قدیمی/نامعتبر/ناموجود، `shipping_method` حتی «تحویل حضوری» و «ارسال فوری»، و `product_id` هیچ نقشی در ورود ندارند. تنظیمات `TESTER_SMS_START_AT` و `TESTER_PACK_PRODUCT_ID` دیگر خوانده نمی‌شوند. هیچ شرط پرداخت جدیدی وجود ندارد.

`order_filter` فقط شرط مشترک pending را نگه می‌دارد؛ `sms_order_filter` کنترل عملیاتی `sms_sent != true` را اضافه می‌کند. ارسال اولیه، Retry و Recovery تأییدنشده از همین محدوده استفاده می‌کنند. قبل از آماده‌سازی و دوباره پس از ساخت Selection، درست پیش از Claim، وضعیت سفارش بررسی می‌شود؛ خروج از pending قبل از این بررسی مانع مصرف تلاش و ارسال می‌شود.

سفارش بدون Notification، با `prepared` یا `retry_pending` موعدرسیده و تعداد تلاش کمتر از سقف انتخاب می‌شود. Notificationهای `accepted`، `exhausted` و `failed` دائمی دوباره ارسال نمی‌شوند؛ شمارهٔ معتبر در `phone` همچنان ضروری است. Claim/Lease و تاریخچهٔ تلاش‌ها تغییر نکرده‌اند. Repair نتیجهٔ از قبل پذیرفته‌شده مستقل از state است و فقط `sms_sent` همان سفارش موجود را اصلاح می‌کند؛ مهلت یا تولید تغییر نمی‌کنند.

API برای ثبت جدید همان شرط pending را در Update اتمیک سفارش بررسی می‌کند. خواندن سفارشِ مرتبط با توکن از شرط ورود جداست: رسید موفق و وضعیت منقضی حتی پس از `not_seen` یا ورود به تولید قابل مشاهده‌اند؛ هیچ انتخاب تازه‌ای خارج از pending نوشته نمی‌شود. بازیابی رسیدِ واقعاً نوشته‌شده قبل از Deadline نیز پس از آزادسازی ممکن است.

### تنظیمات Worker

| متغیر | کاربرد |
| --- | --- |
| `TESTER_ORDERS_COLLECTION` | Collection موجود سفارش‌ها؛ پیش‌فرض `preprocess_orders` |
| `TESTER_WORKER_BATCH_SIZE` | حداکثر سفارش‌های ارسال در هر اجرا؛ پیش‌فرض `50` |
| `TESTER_SMS_ENABLED` | فقط `true` یا `false`؛ پیش‌فرض `false`، مرحلهٔ SMS فقط خواندنی |
| `TESTER_RELEASE_ENABLED` | خالی/تنظیم‌نشده: تابع `TESTER_SMS_ENABLED`؛ `true`: آزادسازی مستقل از SMS؛ `false`: توقف آزادسازی |
| `TESTER_SELECTION_WINDOW_MINUTES` | مهلت مشترک API/Worker از پذیرش؛ عدد صحیح مثبت، پیش‌فرض `30`؛ Intent منجمد تغییر نمی‌کند |
| `TESTER_SMS_TEMPLATE` | وجود هر دو `{full_name}` و `{link}` الزامی؛ Placeholder دیگری مجاز نیست؛ هنگام ارسال ضروری |
| `TESTER_RELEASE_BATCH_SIZE` | سقف مستقل آزادسازی هر اجرا؛ عدد صحیح مثبت، پیش‌فرض `50`؛ با `--limit` تغییر نمی‌کند |
| `TESTER_SELECTION_BASE_URL` | فقط Origin مانند `https://tester.example.com`، بدون مسیر؛ هنگام ارسال الزامی |
| `TESTER_TOKEN_SECRET` | Secret تصادفی و ثابت با حداقل ۳۲ بایت؛ هنگام ارسال الزامی |
| `TESTER_SMS_RETRY_DELAY_MINUTES` | فاصلهٔ Retry به دقیقه؛ فقط عدد صحیح مثبت، پیش‌فرض `5` |
| `TESTER_SMS_MAX_ATTEMPTS` | سقف کل تلاش‌ها، شامل ارسال اول؛ فقط عدد صحیح مثبت، پیش‌فرض `3` |

تنظیمات `MONGODB_*` و `FARAZ_*` همان تنظیمات موجود پروژه هستند. برای ارسال، API Key و شمارهٔ خط فراز باید تنظیم شده باشند.
برای Dry Run، SMS غیرفعال و آزادسازی مستقل، تنظیمات لینک، Secret و اعتبارنامهٔ پیامک ضروری نیستند.
Secret را یک بار مثلاً با `python -c "import secrets; print(secrets.token_urlsafe(48))"` تولید و در `.env` قرار دهید؛ آن را ثابت نگه دارید.
توکن Base64 URL-safe نسخهٔ ۳ از HMAC-SHA256 با همان Secret ثابت، شناسهٔ دارای نوع سفارش و نسخه ساخته می‌شود: خروجی HMAC با `urlsafe_b64encode` کدگذاری می‌شود و ۱۸ کاراکتر اول با همان حروف اصلی نگهداری می‌شوند. در این بخش ابتدایی Padding وجود ندارد. تبدیل عددی و پیمانه‌گیری حذف شده‌اند؛ شمارهٔ نمایشی سفارش و شمارنده مبنای توکن نیستند. بزرگی و کوچکی حروف و علائم در لینک، JavaScript، Body و Hash حفظ می‌شوند. همهٔ Retryهای یک سفارش با Secret و نسخهٔ ثابت دقیقاً همان لینک را می‌فرستند.

Worker در `tester_selections` فقط Hash توکن، شناسهٔ سفارش، نسخه، وضعیت اولیه و زمان ایجاد را درج می‌کند؛ API ثبت نهایی، اطلاعات Intent و انتخاب‌ها را به همان سند اضافه می‌کند. فضای توکن محدود است؛ ایندکس یکتای `token_hash` مانع تخصیص یک توکن به دو سفارش می‌شود. در برخورد نادر توکن، Worker بدون ارسال پیامک برای سفارش دوم با خطای غیرحساس متوقف می‌شود و توکن سفارش اول را دوباره تخصیص نمی‌دهد.

ناسازگاری Secret یا نسخه با Selection موجود، خطای سراسری تنظیمات است؛ Worker پیش از ارسال آن سفارش متوقف می‌شود و Notification را به `failed` دائمی تغییر نمی‌دهد. برای ناسازگاری Secret، مقدار اصلی را بازگردانید. رکوردهای نسخهٔ قبلی خودکار تبدیل یا بازنویسی نمی‌شوند؛ هیچ Migration روی داده‌های موجود انجام نمی‌شود.

### ایندکس‌ها و اجرای یک نوبت

پس از تنظیم Environment، ایندکس‌ها را یک بار با فرمان مستقل ایجاد کنید؛ اجرای دوبارهٔ این فرمان ایندکس‌های موجود را بررسی/بازاستفاده می‌کند:

```shell
python -m app.workers.tester_sms --create-indexes
```

این فرمان روی سفارش‌ها ایندکس جدید `tester_pending_orders` با کلیدهای `(state, _id)` صعودی، Collation ساده و بدون Partial/Sparse ایجاد می‌کند. Query ارسال به همین نام Hint می‌دهد و پس از برابری state، بر اساس `_id` مرتب می‌شود؛ بنابراین نبود جنسیت یا تاریخ هیچ سفارشی را از ایندکس حذف نمی‌کند. Limit همچنان بعد از Join و کنترل Notification اعمال می‌شود.

در `tester_sms_notifications` ایندکس یکتای `order_id` و ایندکس‌های بازیابی/اصلاح/Retry، و در `tester_selections` ایندکس‌های یکتای `order_id` و `token_hash` حفظ می‌شوند. صف آزادسازی از Notificationهای `accepted` با Hint موجود `tester_notification_repair` روی `(status, order_synced, accepted_at)` شروع می‌شود. سفارش با `_id_` و رسید با `tester_selection_order_unique` خوانده می‌شوند؛ صف آزادسازی مستقل از ارسال است و `sms_sent=true` مانع آن نیست.

`check_indexes` نام، کلیدها، نبود Partial/Sparse و Collation ایندکس سفارش را کنترل می‌کند؛ `check_release_index` ایندکس مبدأ آزادسازی را بررسی می‌کند. هیچ ایندکسی هنگام Import، اجرای عادی یا Dry Run خودکار ساخته نمی‌شود؛ ایندکس جدید فقط با `--create-indexes` ایجاد می‌شود. در نبود ایندکس سازگار، ارسال پیش از Claim با پیام اجرای این فرمان متوقف می‌شود.

پیش از استقرار، Cron/نسخهٔ قبلی را متوقف کنید، ایندکس جدید را با فرمان بالا بسازید، Dry Run و Explain را بررسی و سپس Cron جدید را فعال کنید. حذف Cutoff عمداً دامنه را گسترش می‌دهد: **همهٔ سفارش‌های قدیمیِ هنوز pending** که کنترل‌های عملیاتی را پاس کنند می‌توانند SMS بگیرند یا در صورت پذیرش قبلی و پایان مهلت آزاد شوند. Batch مستقل ارسال/آزادسازی حجم هر اجرا را محدود می‌کند؛ ابتدا صف را با SMS غیرفعال و Dry Run بررسی کنید. Secret، دامنه و وضعیت Notificationها را حفظ کنید.

ایندکس‌های قدیمی `tester_gender_created`، `tester_pending_release` و `tester_product_created` خودکار حذف، بازسازی یا استفاده نمی‌شوند. نگه‌داشتن آن‌ها مانع کار نسخهٔ جدید نیست. برای حذف اختیاری در آینده، ابتدا `getIndexes()` و مصرف سایر سرویس‌ها را بررسی کنید و پس از اطمینان از استقرار کامل فقط همان نام مشخص را دستی حذف کنید؛ از `dropIndexes()` استفاده نکنید. این تغییر هیچ Migration یا Bulk Update روی داده‌های Production اجرا نمی‌کند.

فیلتر و Join با Notification در MongoDB انجام می‌شوند؛ Limit بعد از حذف وضعیت‌های غیرقابل ارسال و Retryهای زودتر از موعد اعمال می‌شود.
Cursor مرحله‌ای خوانده می‌شود، `skip` استفاده نمی‌شود و پردازش همهٔ سفارش‌ها در حافظه انجام نمی‌شود.

Dry Run خلاصهٔ تعداد سفارش‌های SMS تا سقف Limit و، در صورت فعال‌بودن آزادسازی، `release_ready` تا سقف Batch مستقل را نشان می‌دهد. شمارنده‌های `released_*` صفر می‌مانند؛ هیچ سند، توکن، Selection، Notification، ایندکس یا پیامکی ایجاد/تغییر نمی‌کند:

```shell
python -m app.workers.tester_sms --dry-run --limit 10
```

اجرای دستی یک نوبت:

```shell
python -m app.workers.tester_sms --once --limit 50
```

`--limit` سقف پیش‌فرض Batch را جایگزین می‌کند. پیش از ارسال واقعی، `TESTER_SMS_ENABLED=true` را خودتان تنظیم کنید.
با مقدار `false` و تنظیم خالی/تنظیم‌نشدهٔ `TESTER_RELEASE_ENABLED`، حتی `--once` فقط خلاصهٔ خواندنی می‌دهد. اگر صریحاً `TESTER_RELEASE_ENABLED=true` باشد، فقط آزادسازی اجازهٔ تغییر سفارش دارد؛ هیچ ارسال یا Repair پیامکی انجام نمی‌شود. `--dry-run` در همهٔ ترکیب‌ها کاملاً خواندنی است. `read_only` در JSON مشخص می‌کند کل اجرا خواندنی بوده است یا نه.
اصلاح سفارش‌های پذیرفته‌شده و بازیابی وضعیت‌های معلق، هر کدام نیز تا سقف Limit در همان اجرا انجام می‌شوند.
هیچ API یا Worker همیشگی راه‌اندازی نمی‌شود؛ در اجرای مستقل، اتصال AsyncMongoClient ایجاد و در پایان بسته می‌شود.

نمونهٔ Cron در Linux، هر دقیقه؛ همان Worker به‌صورت One-Shot اجرا می‌شود. مسیر پروژه را جایگزین کنید:

```cron
* * * * * cd /srv/TesterPack_Page && /srv/TesterPack_Page/.venv/bin/python -m app.workers.tester_sms --once >> /srv/TesterPack_Page/tester-worker.log 2>&1
```

### آزادسازی سفارش برای تولید

`release_resolved_tester_orders` صف مستقلی از ارسال SMS دارد. فقط state دقیقاً برابر رشتهٔ `pending` با Update شرطی همان `_id` واقعی به `not_seen` تبدیل می‌شود. جنسیت، تاریخ خرید و روش ارسال فیلتر نمی‌شوند؛ `recommended_products`، Submission Marker، Timestamp و Audit هرگز بازنویسی نمی‌شوند.

هر دو مسیر به Notification دارای `status=accepted` و `accepted_at` از نوع BSON Date معتبر و غیرآینده نیاز دارند:

| وضعیت انتخاب | قبل از `accepted_at + TESTER_SELECTION_WINDOW_MINUTES` | دقیقاً در Deadline و بعد از آن | تغییر `tester_gender` |
| --- | --- | --- | --- |
| `submitted` معتبر، نوشته‌شده پیش از Deadline و سازگار با سفارش | آزادسازی | آزادسازی با همان اولویت Submit موفق | `"تستر انتخابی"` |
| `submitting` یا `review_required` با اثبات ثبت واقعی و به‌موقع سفارش | آزادسازی | آزادسازی با اولویت ثبت معتبر | `"تستر انتخابی"` |
| Intent بدون ثبت معتبر، `pending` یا Selection ناموجود | بدون تغییر | آزادسازی مستقل از Selection | بدون تغییر، حتی null، خالی یا ناموجود |
| رسید `submitted` ناقص، دیرهنگام یا ناسازگار | بدون تغییر | آزادسازی از مسیر انقضا | بدون تغییر |

اثبات ثبت معتبر شامل شناسهٔ معتبر Submission، دقیقاً ۱۰ PID معتبر و یکتا با همان قواعد API، ۱۰ نام معتبر، تطابق کامل نام‌ها و Marker با سفارش و `accepted_at <= submitting_at <= tester_selection_written_at < Deadline` است. برای `submitted`، وجود `submitted_at` معتبر و برابر با زمان ثبت سفارش همچنان الزامی است؛ برای `submitting` و `review_required`، نبود این فیلد مانع نیست چون ثبت واقعی در سفارش اثبات می‌شود. Deadline منجمد Intent حفظ می‌شود و در نبود آن، تنظیم مشترک مبنا است. صرف وجود `recommended_products` یا Intent ناقص کافی نیست. بررسی ساختار مدارک با `$jsonSchema` در Query و سپس بررسی مجدد سفارش و Selection پیش از Update انجام می‌شود؛ Validator یا مدل جدیدی ساخته نمی‌شود. موجودی فعلی کاتالوگ دوباره مبنای رد رسید ذخیره‌شده قرار نمی‌گیرد.

مسیر موفق، حتی اگر Worker چند ساعت دیر اجرا شود، یک `$set` اتمیک روی سفارش دارد:

```javascript
{ $set: { state: "not_seen", tester_gender: "تستر انتخابی" } }
```

این مسیر انقضای زمان اجرای Worker را ملاک رد ثبت به‌موقع قرار نمی‌دهد. خواندن مدارک آزادسازی فقط از Primary، به‌ترتیب **سفارش، سپس Selection** است؛ ترتیب نوشتن API هم Intent ثابت و سپس اطلاعات سفارش است. بنابراین Selection قدیمی با تصویر تازه‌تر سفارش ترکیب نمی‌شود. ساعت خواندن و شرط Update از `$$NOW` MongoDB می‌آیند.

هر دو Update موفق و منقضی، علاوه بر state دقیق pending، Snapshot کامل سه فیلد `recommended_products`، `tester_selection_submission_id` و `tester_selection_written_at` را اتمیک مقایسه می‌کنند؛ وجود/نبود فیلد هم جدا بررسی می‌شود تا missing و null یکی محسوب نشوند. اگر API بین Read و Update مدارک سفارش را بنویسد یا تغییر دهد، Update قدیمی Match نمی‌شود. Worker تا سه بار مدارک را از نو می‌خواند و مسیر را دوباره تعیین می‌کند؛ هرگز با تصمیم قدیمی به مسیر انقضا Fall back نمی‌کند. تغییر مداوم مدارک پس از این سقف، برای سفارش هنوز pending یک `release_errors` و لاگ عمومی ایجاد می‌کند و آزادسازی به اجرای بعد واگذار می‌شود؛ سایر سفارش‌ها متوقف نمی‌شوند. سفارشِ هم‌زمان آزادشده یا وارد تولیدشده فقط Skip می‌شود و هیچ فیلدی از آن بازنویسی نمی‌شود.

مسیر انقضا فقط با `$$NOW >= Deadline` (مهلت منجمد یا تنظیم مشترک) و `$set: {state: "not_seen"}` اجرا می‌شود و جنسیت را حتی در صورت نبود فیلد ایجاد نمی‌کند. اگر مدارک واقعی معتبر باشند، عبور از Deadline یا تأخیر Finalization مانع تغییر اتمیک هر دو فیلد نمی‌شود. نام شمارندهٔ `released_submitted` حفظ شده و شامل هر ثبت واقعیِ معتبر، حتی Selection نیمه‌تمام، است؛ `released_expired` فقط نبود ثبت معتبر را می‌شمارد. هر دو فقط از `modified_count` واقعی افزایش می‌یابند.

پس از انقضا، `submitting` و `review_required` هم سفارش را متوقف نمی‌کنند. در مسیر انقضای بدون ثبت معتبر، آزادسازی به معنی حل مشکل پشتیبانی یا تأیید انتخاب نیست: تمام Intent و Audit باقی می‌مانند، انتخاب واقعاً نوشته‌شده حفظ می‌شود و مقدار پیش‌فرضی در `recommended_products` ساخته نمی‌شود. Recovery رسیدِ نوشته‌شده پیش از Deadline و مشاهدهٔ مشتری همچنان ممکن است؛ ثبت انتخاب جدید بعد از Deadline همچنان ممنوع است.

Worker وضعیت Selection را Finalize یا بازنویسی نمی‌کند؛ API همچنان رسید نوشته‌شدهٔ به‌موقع را بعد از `not_seen` بازیابی می‌کند. این هماهنگی به ثابت‌بودن Intent پس از تخصیص و نوشتن هر سه فیلد سفارش در همان عملیات اتمیک API متکی است. ویرایش مستقیم و هم‌زمان Intent یا `accepted_at` خارج از این قرارداد، Transaction چندسندی ندارد؛ هیچ ابزار ویرایش/اصلاح خودکاری برای آن اضافه نشده است.

اگر پیامک هیچ‌گاه Accepted نشده یا `accepted_at` معتبر ندارد، سفارش **Deadline معتبر ندارد** و به دلیل گذشت زمان از `created_at` یا تاریخ خرید آزاد نمی‌شود. تعیین تکلیف این سفارش‌ها به تصمیم کسب‌وکاری جداگانه نیاز دارد؛ Retry و گزارش `exhausted` فعلی حفظ می‌شوند.

Batch آزادسازی مستقل است و ظرفیت ارسال SMS را کم نمی‌کند. Cursor از ایندکس Notificationهای پذیرفته‌شده شروع می‌شود؛ سفارش بدون Notification یا با ارسال ناموفق وارد Lookup سفارش نمی‌شود. Lookup ایندکس‌شدهٔ Selection قبل از کنارگذاشتن سفارشِ بدون انتخاب انجام می‌شود تا Deadline منجمد، حتی پس از تغییر تنظیمات، بررسی شود؛ اعتبارسنجی کامل مدارک و Limit پس از تعیین Deadline مؤثر هستند. Lookupهای ایندکس‌شده و شرط آماده‌بودن قبل از Sort/Limit اجرا می‌شوند. هر صفحه محدود است و حداکثر `TESTER_RELEASE_BATCH_SIZE` سفارش در یک اجرا واقعاً آزاد می‌شود. در خطا یا رقابت، صفحهٔ بعد با Keyset از `_id` ادامه پیدا می‌کند؛ بنابراین حتی Batch یک‌تایی و یک سفارش دائماً خطادار، سفارش‌های سالم بعدی را پشت Limit نگه نمی‌دارند. تعداد Notificationهای `accepted`، شامل سفارش‌های قبلاً آزادشده، هنوز بر هزینهٔ Query اثر دارد؛ برای هرکدام وضعیت فعلی سفارش با Lookup بررسی می‌شود. خطای Query مرحلهٔ آزادسازی نیز مانع تلاش مرحلهٔ SMS نیست. خطاهای فراز و حتی اعتبارنامهٔ نامعتبر SMS پس از اجرای آزادسازی بررسی می‌شوند.

خروجی JSON شامل `released_submitted`، `released_expired`، `release_ready`، `release_skipped`، `release_errors` و `release_enabled` است. `release_ready` تعداد کاندیداهای خوانده‌شده است و هنگام خطا/رقابت ممکن است از سقف موفقیت بیشتر شود. `release_skipped` شامل رقابت با Worker دیگر یا تغییر شرط هنگام Update است؛ خطای عمومی بدون اطلاعات مشتری لاگ می‌شود. در اجرای بعدی سفارش موفق دوباره شمارش نمی‌شود.
اگر `release_errors>0` باشد، مرحلهٔ SMS همچنان اجرا می‌شود، سپس خلاصهٔ JSON چاپ، تعداد خطاها بدون جزئیات محرمانه در لاگ `ERROR` ثبت و Exit Code برابر `1` می‌شود. اجرای سالم همچنان `0` و توقف ناشی از HTTP سراسری فراز همچنان `1` است؛ منطق Retry تغییری ندارد.

برای Preview آزادسازی بدون ارسال SMS، در همان محیط اجرا `TESTER_SMS_ENABLED=false` و `TESTER_RELEASE_ENABLED=true` را تنظیم و فرمان زیر را اجرا کنید؛ ابتدا ایندکس‌ها باید از مسیر `--create-indexes` آماده شده باشند:

```shell
python -m app.workers.tester_sms --dry-run --limit 10
```

پس از بررسی، همان `--once` مرحلهٔ آزادسازی مستقل را اجرا می‌کند. غیرفعال‌کردن صریح آزادسازی با `TESTER_RELEASE_ENABLED=false` ممکن است. این تنظیمات Production در اجرای تست‌ها تغییر داده نمی‌شوند.

#### شناسایی فقط‌خواندنی سفارش‌های آزادشده با جنسیت اشتباه

نمونهٔ زیر فقط شناسهٔ حداکثر ۲۰۰ سفارش `not_seen` را گزارش می‌دهد که مدارک واقعی و معتبر انتخاب دارند ولی جنسیتشان دقیقاً `تستر انتخابی` نیست؛ شامل فیلد ناموجود و null هم می‌شود. از همان Query اعتبارسنجی Worker استفاده می‌کند و هیچ Update، Migration، SMS یا ساخت ایندکس ندارد. برای بررسی مرحله‌های بعد تولید، ابتدا مقدار دقیق state آن سیستم را مشخص کنید؛ این نمونه فقط `not_seen` را بررسی می‌کند. این بررسی روی Production در جریان توسعه اجرا نشده است.

```python
import asyncio
from pymongo import AsyncMongoClient, ReadPreference
from app.config import MONGODB_URI, MONGODB_DB_NAME, MONGODB_TIMEOUT_MS
from app.workers.tester_sms import (
    NOTIFICATIONS, RELEASE_SOURCE_INDEX, WorkerSettings, release_pipeline,
)

async def audit():
    settings = WorkerSettings.from_environment()
    client = AsyncMongoClient(
        MONGODB_URI, tz_aware=True, timeoutMS=MONGODB_TIMEOUT_MS,
        read_preference=ReadPreference.PRIMARY,
    )
    try:
        pipeline = release_pipeline(settings, limit=200)
        scope = next(stage["$match"] for stage in pipeline
                     if stage.get("$match", {}).get("state") == "pending")
        scope["state"] = "not_seen"
        scope["$expr"] = {"$and": [scope["$expr"], {
            "$ne": [{"$ifNull": ["$tester_gender", None]}, "تستر انتخابی"],
        }]}
        # Reuse the complete proof branch, including PID/name JSON Schema checks.
        valid_proof = pipeline[-4]["$match"]["$or"][1]
        pipeline.insert(-3, {"$match": valid_proof})
        cursor = await client[MONGODB_DB_NAME][NOTIFICATIONS].aggregate(
            pipeline, hint=RELEASE_SOURCE_INDEX, collation={"locale": "simple"},
            batchSize=50, maxTimeMS=5000,
        )
        async for row in cursor:
            print(str(row["_id"]))
    finally:
        await client.close()

asyncio.run(audit())
```

### وضعیت ارسال و خطاها

| وضعیت | معنی |
| --- | --- |
| `prepared` | رکورد اولیه ایجاد شده، درخواست ارسال هنوز آغاز نشده است |
| `sending` | یک Worker با Claim اتمیک مالک تلاش ارسال شده است |
| `retry_pending` | موفقیت قطعی ثبت نشده؛ منتظر `next_retry_at` و تلاش بعدی |
| `accepted` | فراز درخواست را پذیرفته و پاسخ موفق به‌صورت پایدار ثبت شده است |
| `exhausted` | سقف تلاش‌ها تمام شده و موفقیت قطعی ثبت نشده؛ قابل گزارش‌گیری |
| `failed` | خطای دائمی دادهٔ سفارش، مثل موبایل نامعتبر |

Claim با تغییر شرطی `prepared` یا Retry رسیده به `sending`، افزایش `attempt_count` و ساخت `attempt_id` جدید در یک سند انجام می‌شود؛ [MongoDB این تغییر تک‌سندی را اتمیک اجرا می‌کند](https://www.mongodb.com/docs/manual/core/write-operations-atomicity/).
نوشتن‌ها با Write Concern برابر `majority` انجام می‌شوند و فراخوانی فراز فقط بعد از تأیید Claim انجام می‌شود.
Lease برابر Timeout فراز به‌علاوهٔ Grace Period پنج‌دقیقه‌ای است؛ Worker فعال هنگام انتظار برای پاسخ، Lease را تمدید می‌کند.
پس از Crash و انقضای Lease، اجرای بعدی تلاش قبلی را پایان‌یافته ثبت و سفارش را به `retry_pending` یا `exhausted` می‌برد.
پس از ثبت خطا/بازیابی، `next_retry_at` برابر زمان فعلی به‌علاوهٔ فاصلهٔ Retry است؛ اجرای Cron بعد از رسیدن آن موعد می‌تواند ارسال کند.
نتیجه فقط با همان `attempt_id` ذخیره می‌شود؛ پاسخ دیررسِ تلاش قبلی بعد از Claim جدید نمی‌تواند وضعیت یا `sms_sent` را تغییر دهد.
پاسخ موفق دیررس، اگر هنوز تلاش جدید Claim نشده باشد، می‌تواند همان تلاش را به `accepted` ببرد.

تمام خطاهای فراز، Timeout، قطع اتصال و پاسخ نامشخص تا سقف تلاش‌ها Retry می‌شوند. دریافت پیامک تکراری در نتیجهٔ مبهم ممکن است؛ اولویت، از دست نرفتن پیامک است.
شماره یا دادهٔ نامعتبر، وضعیت دائمی `failed` می‌گیرد؛ خطای عادی یک سفارش پردازش سایر سفارش‌ها را متوقف نمی‌کند.
در `401` و `403` (تنظیمات سراسری) و `429` (محدودیت ارسال)، نتیجهٔ تلاش فعلی ثبت و اجرای جاری متوقف می‌شود؛ Retry آینده تا سقف مجاز حفظ می‌شود.
خلاصهٔ اجرا `halted_http_status` دارد و فرمان با Exit Code یک خارج می‌شود. ابتدا اعتبارنامه/محدودیت فراز را اصلاح کنید؛ سفارش‌های بعدی در آن نوبت تلاش بیهوده ندارند.

بازیابی تلاش‌های تأییدنشدهٔ قدیمی فقط برای سفارش‌های هنوز pending و بدون `sms_sent=true`، به‌صورت تدریجی و بدون Migration یا Bulk Update انجام می‌شود.
`submission_unknown` و `failed` قدیمی با کدی غیر از `invalid_order_data` قابل بازیابی‌اند؛ در نبود `attempt_count`، یک تلاش قبلی برای آن‌ها منظور می‌شود.
موعد آن‌ها از زمان آخرین تلاش/آپدیت به‌علاوهٔ فاصلهٔ Retry ساخته می‌شود؛ اگر زمان قبلی وجود نداشته باشد از زمان بازیابی محاسبه می‌شود.
اگر `error_code` یا `last_error_code` برابر `invalid_order_data` باشد، `failed` دائمی باقی می‌ماند؛ کد خطای جاری در هر دو فیلد یکسان ثبت می‌شود. تاریخ سفارش در بازیابی نقشی ندارد؛ خروج از pending جلوی Retry را می‌گیرد.
اگر سقف تنظیم‌شده کاهش یابد، Retryهای با تعداد تلاش بیشتر یا مساوی سقف به `exhausted` تبدیل می‌شوند.

اطلاعات تلاش‌ها در همان `tester_sms_notifications` نگهداری می‌شوند: `attempt_count`، `next_retry_at`، `last_attempt_at`، `last_error_code`، Lease، شناسهٔ تلاش و وضعیت پذیرش/همگام‌سازی.
آرایهٔ `attempts` شامل شماره/شناسهٔ تلاش، زمان شروع/پایان، نتیجه، HTTP Status و کد خطای غیرحساس است. موبایل، متن، لینک، توکن و بدنهٔ پاسخ در آن ذخیره نمی‌شوند.
تمام Retryها همان Selection و لینک HMAC قبلی را استفاده می‌کنند.

پس از موفقیت، فقط HTTP Status، `data` عددی و زمان پذیرش در Notification ثبت می‌شود؛ سپس `sms_sent=true` روی همان سفارش موجود نوشته می‌شود؛ پذیرش ثبت‌شده حتی پس از تغییر state نیاز به Repair دارد.
اگر مرحلهٔ دوم شکست بخورد، اجرای بعدی فقط فیلد سفارش را اصلاح می‌کند و پیامک دوباره نمی‌فرستد.
اگر پاسخ موفق قبل از ثبت `accepted` در MongoDB گم شود، پس از بازیابی Lease و موعد Retry ارسال دوباره مجاز است؛ `sms_sent` تا ثبت موفقیت قطعی تغییر نمی‌کند.
Repair پذیرش قبلی به جنسیت، تاریخ، روش ارسال یا state وابسته نیست و فقط روی `_id` همان سفارش انجام می‌شود؛ سفارش حذف‌شده اصلاح نمی‌شود و `order_sync_blocked=order_missing_or_ineligible` می‌گیرد.
`sms_sent=true` یعنی پذیرش درخواست توسط فراز، نه تأیید تحویل به گوشی.
لاگ‌ها فقط خلاصه و پیام خطای عمومی دارند؛ شماره، Secret، API Key، توکن، لینک اختصاصی یا بدنهٔ پاسخ ثبت نمی‌شوند.

تعداد سفارش‌های هنوز pending با وضعیت `exhausted` در خلاصهٔ هر اجرا با `exhausted_total` گزارش می‌شود. گزارش خواندنی با شناسهٔ سفارش، تعداد تلاش و آخرین کد خطا:

```shell
python -m app.workers.tester_sms --report-exhausted --limit 50
```

### تست‌های Mock و Integration

تست‌های Mock؛ تست‌های Integration در اجرای پیش‌فرض Skip می‌شوند:

```shell
python -m unittest discover -s tests -v
```

تست واقعی MongoDB با همان URI پروژه در دیتابیس موقت `tester_worker_it_<uuid>` اجرا می‌شود؛ هیچ سفارش واقعی خوانده یا تغییر داده نمی‌شود و تمام درخواست‌های فراز Mock هستند.
کاربر MongoDB باید اجازهٔ ایجاد و حذف دیتابیس تست را داشته باشد. دیتابیس‌های ساخته‌شده حتی در شکست تست پاک می‌شوند.

```powershell
$env:TESTER_RUN_MONGO_INTEGRATION = "1"
python -m unittest tests.test_tester_sms_integration tests.test_tester_release_integration -v
Remove-Item Env:TESTER_RUN_MONGO_INTEGRATION
```

در Linux:

```shell
TESTER_RUN_MONGO_INTEGRATION=1 python -m unittest tests.test_tester_sms_integration tests.test_tester_release_integration -v
```

قطع ارتباط با بستن واقعی کلاینت MongoDB و اتصال دوباره آزمایش می‌شود؛ سرویس MongoDB مشترک متوقف نمی‌شود.
Integration شامل state دقیق (و رد آرایه/نوع متفاوت)، مستقل‌بودن از تمام انواع جنسیت، تاریخ قدیمی یا ناموجود، روش ارسال حضوری/فوری، ایندکس جدید بدون Partial، حفظ ایندکس‌های قدیمی، `$lookup`، Claim هم‌زمان، Retry، Repair بعد از آزادسازی و Dry Run کاملاً خواندنی است. تست‌های Selection، ثبت ۱۰ نام دقیق، مشاهدهٔ رسید/انقضا بعد از تغییر state و جلوگیری اتمیک از ثبت جدید خارج از pending را نیز پوشش می‌دهند. آزادسازی Submit معتبرِ پیش از Deadline در اجرای دیرهنگام، تغییر اتمیک جنسیت و state و حفظ جنسیت در تمام مسیرهای انقضا تست می‌شوند.
خلاصهٔ Explain روی دادهٔ ساختگی در `test-artifacts/tester-worker-explain.json` ذخیره می‌شود؛ این پوشه وارد Git نمی‌شود.
تست‌های آزادسازی در `tests.test_tester_release` و `tests.test_tester_release_integration` قرار دارند؛ Integration فقط دیتابیس موقت `tester_release_it_<uuid>` را تغییر می‌دهد و پس از اجرا حذف می‌کند. سناریوها شامل مرز برابر Deadline، همهٔ وضعیت‌ها، نبود زمان معتبر، Batch کوچک و رسید خراب، دو Worker هم‌زمان، حفظ Audit/Recovery، Dry Run و شکست قابل بازیابی دیتابیس است. Explain صف آزادسازی در `test-artifacts/tester-release-explain.json` ذخیره می‌شود؛ هیچ درخواست واقعی فراز در این تست‌ها مجاز نیست.

تست Performance مستقل و اختیاری، ۲۰٬۰۰۰ سفارش `pending` در دیتابیس موقت `tester_release_perf_<uuid>` می‌سازد: ۸٬۰۰۰ بدون Notification، ۶٬۰۰۰ با Notification ناموفق، ۵٬۹۰۰ پذیرفته‌شده و هنوز منتظر انتخاب، ۵۰ منقضی و ۵۰ رسید معتبر `submitted`. سفارش‌های آماده عمداً انتهای ترتیب هستند. Query سه بار برای Batch پنجاه‌تایی و سه بار پس از خالی‌شدن صف آماده اجرا می‌شود؛ هیچ SMS واقعی ارسال نمی‌شود و دیتابیس تست در پایان حذف می‌شود:

```powershell
$env:TESTER_RUN_RELEASE_PERFORMANCE = "1"
python -m unittest tests.test_tester_release_performance -v
Remove-Item Env:TESTER_RUN_RELEASE_PERFORMANCE
```

اندازه‌گیری همین محیط روی MongoDB `8.0.1` در ۱۸ اکتبر ۲۰۲۶، قبل/بعد از بهینه‌سازی:

| حالت | میانهٔ Query قبل/بعد (ms) | مجموع DocsExamined قبل/بعد | مجموع KeysExamined قبل/بعد | بررسی ردیفی Lookup قبل/بعد |
| --- | --- | --- | --- | --- |
| آماده‌ها در انتهای ۲۰٬۰۰۰ سفارش؛ خروجی ۵۰ | 9973.34 / 803.15 | 37900 / 12100 | 37900 / 12101 | 25900 / 6100 |
| بدون سفارش آماده؛ ۱۹٬۹۰۰ Pending باقی‌مانده | 11264.40 / 827.47 | 37700 / 12000 | 37700 / 12001 | 25800 / 6000 |

در نسخهٔ بهینه، زمان سه اجرای حالت اول `803.15, 612.22, 1069.36 ms` و حالت دوم `827.47, 642.98, 1159.25 ms` بود. هر Query دو مرحلهٔ Lookup دارد؛ «بررسی ردیفی» جمع `nReturned` آن مرحله‌هاست، نه تعداد درخواست شبکه. مجموع Docs/Keys، Cursor مبدأ و هر دو Lookup را جمع می‌کند. Cursor مبدأ ۶٬۰۰۰ Notification پذیرفته‌شده را بررسی کرد؛ Lookup سفارش با `_id_` و Lookup رسید با ایندکس یکتای سفارش انجام شد. در حالت بدون کار، Lookup رسید هیچ ردیفی پردازش نکرد؛ هیچ Collection Scan در Lookupها نبود. نتایج، Benchmark همین محیط و توزیع داده هستند و تضمین زمان ثابت برای حجم‌های دیگر نیستند. Timeout Production تغییر نکرده است؛ بودجهٔ بزرگ‌تر فقط در تست اجازهٔ اندازه‌گیری Query کند قبلی را می‌دهد.

گزارش JSON و Explain در `test-artifacts/tester-release-performance.json` و `test-artifacts/tester-release-performance-explain.json` ذخیره می‌شوند؛ این پوشه وارد Git نمی‌شود.

پس از قابل تنظیم‌شدن مهلت، Benchmark بیست‌هزار سفارشی در ۲۶ اکتبر ۲۰۲۶ نیز Pass شد: میانهٔ Query برای خروجی ۵۰ سفارش `1967.81 ms` و در حالت بدون کار `1363.21 ms` بود؛ مجموع Docs/Keys به‌ترتیب `18000/18001` و `17900/17901` شد. برای رعایت Deadline منجمد، Selection سفارش‌های هنوز در مهلت هم با ایندکس خوانده می‌شود؛ این افزایش Lookup ضروری است و هیچ Collection Scan در Lookupها ایجاد نشده است. Timeout Production و ایندکس‌ها تغییر نکرده‌اند.

پس از یکسان‌سازی Fallback نام مشتری و الزامی‌شدن هر دو Placeholder، همین Benchmark روی MongoDB `8.0.1` در دیتابیس‌های موقت مستقل دوباره Pass شد. در دو اجرای مستقل، بدون اجرای هم‌زمان تست‌های دیگر، میانهٔ سه Query برای خروجی ۵۰ سفارش به‌ترتیب `1645.99` و `2161.38 ms` و برای حالت بدون کار `2246.53` و `1917.63 ms` بود. مجموع Docs/Keys همچنان `18000/18001` و `17900/17901`، بررسی ردیفی Lookupها `12000` و `11900` و تعداد Collection Scan در Lookupها صفر بود. Query، ایندکس‌ها و منطق Release در این اصلاح تغییر نکرده‌اند؛ هزینهٔ بررسی داده افزایش نیافته، اما زمان حالت بدون کار نسبت به نمونهٔ قبلی حدود ۴۱ تا ۶۵ درصد بیشتر است. با توجه به نوسان زمان‌ها، نبود افت زمان اجرا را نمی‌توان قطعی اعلام کرد؛ این اندازه‌گیری‌ها تضمین زمان Production نیستند. هر ۷۳ تست مرتبط Integration پس از اجرای مجدد سه مورد ناموفق در اجرای موازی، پاس شدند؛ دو مورد با Timeout و یک مورد با توقف زودهنگام پردازش روبه‌رو شده بودند. Timeout برنامه تغییر نکرد و هیچ SMS واقعی ارسال نشد.

پس از تغییر معیار ورود به state، همین Benchmark در ۲۱ اکتبر ۲۰۲۶ روی MongoDB `8.0.1` دوباره اجرا و Pass شد: میانهٔ Query برای خروجی ۵۰ سفارش `1116.35 ms`، مجموع Docs/Keys برابر `12100/12101` و Lookup ردیفی `6100` بود؛ در حالت بدون سفارش آماده، میانه `891.91 ms`، مجموع Docs/Keys برابر `12000/12001` و Lookup ردیفی `6000` بود. در هر دو حالت مبدأ فقط ۶٬۰۰۰ Notification پذیرفته‌شده را بررسی کرد و Lookupها Collection Scan نداشتند. Explain ارسال نیز در Fixture شامل ۳٬۰۰۱ سفارش، `IXSCAN` روی ایندکس جدید، ۳۱ Docs/Keys بررسی‌شده و خروجی ۱۰ سفارش را تأیید کرد. این اعداد زمان Query هستند؛ بررسی مجدد رسید و Updateهای آزادسازی جداگانه انجام می‌شوند.

## ساختار

```text
app/
  __init__.py
  config.py       # خواندن تنظیمات
  main.py         # برنامه، Lifespan و health
  catalog.py      # Query دسته‌ای، کش و API کاتالوگ
  catalog_labels.py # نگاشت و خلاصه‌سازی فارسی
  selections.py   # Context، ثبت اتمیک و Recovery انتخاب سفارش
  selection_security.py # هدرهای خصوصی، Rate Limit و Redact لاگ
  workers/
    __init__.py
    tester_sms.py # Worker مستقل و یک‌نوبتی
  services/
    __init__.py
    sms/
      __init__.py
      faraz.py    # ارسال پیامک ساده به یک گیرنده
frontend/
  index.html
  app.js          # کاتالوگ، سفارش واقعی، ثبت نهایی و رسید فقط‌خواندنی
  styles.css
  perfume-details.css
  catalog.css     # فقط حالت‌های بارگذاری/خطا
  selection.css   # وضعیت سفارش، تأیید نهایی و رسید
  assets/
    perfume-placeholder.svg
tests/
  __init__.py
  test_catalog.py
  test_catalog_integration.py
  test_catalog_frontend.cjs
  test_selections.py
  test_selection_routes.py
  test_selections_integration.py
  test_selection_frontend.cjs
  selection_preview.py
  test_sms_faraz.py
  test_tester_sms_worker.py
  test_tester_sms_integration.py
.env              # تنظیمات محلی؛ خارج از Git
.env.example
.gitignore
requirements.txt
README.md
```

تست‌های تنظیمات شامل تبدیل Line Break فارسی، قالب نامعتبر، نام کامل و fallback، Retry با Payload یکسان، مهلت قابل تنظیم و هماهنگی API/Worker، حفظ Deadline منجمد در افزایش/کاهش تنظیمات و Recovery ثبت به‌موقع پس از کاهش مهلت هستند.

### آزادسازی نهایی بر اساس زمان پرداخت

در پایان اجرای عادی Worker، بعد از آزادسازی قبلی و پردازش SMS، تمام سفارش‌های باقی‌مانده با `state` دقیقاً برابر رشتهٔ `pending` و گذشت حداقل ۴۰ دقیقه از `date_paid` با Update شرطی به `not_seen` تغییر می‌کنند. فقط `state` تغییر می‌کند و این شرط به Notification، پذیرش پیامک یا وضعیت Selection وابسته نیست. این مرحله از `TESTER_RELEASE_ENABLED` پیروی می‌کند و محدودیت Batch پیامک روی آن اعمال نمی‌شود.

مقایسه با ساعت MongoDB (`$$NOW`) انجام می‌شود. `date_paid` از نوع BSON Date یا رشتهٔ تاریخ میلادی قابل تبدیل به Date پشتیبانی می‌شود؛ Offset موجود در رشته رعایت می‌شود و رشتهٔ بدون منطقهٔ زمانی UTC محسوب می‌شود. فیلد خالی، نامعتبر، آرایه یا مقدار عددی باعث آزادسازی نمی‌شود. حالت `--dry-run` فقط تعداد را گزارش می‌دهد. Worker یک‌بار اجرا می‌شود؛ تغییر وضعیت در اولین اجرای آن پس از رسیدن به مرز ۴۰ دقیقه انجام خواهد شد.

خلاصهٔ JSON شمارنده‌های `released_paid_timeout`، `paid_timeout_ready` و `paid_timeout_errors` را نیز دارد. خطای دیتابیس این مرحله در `release_errors` جمع می‌شود و Exit Code اجرای Worker را ناموفق می‌کند تا اجرای بعدی دوباره تلاش کند.