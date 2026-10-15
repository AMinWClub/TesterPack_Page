# انتخاب تسترهای سوراد

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

برای بررسی فرانت با Node 22+ و مرورگر Chromium نصب‌شده، ابتدا در یک ترمینال سرور را اجرا کنید و سپس تست را در ترمینال دیگر اجرا کنید:

```shell
uvicorn app.main:app --host 127.0.0.1 --port 8787
node tests/test_catalog_frontend.cjs
```

تست در Windows به‌صورت پیش‌فرض Edge را پیدا می‌کند؛ برای مسیر دیگر `CATALOG_BROWSER_PATH` را تنظیم کنید.
آدرس سرور با `CATALOG_TEST_BASE_URL` قابل تغییر است. بررسی اولیهٔ UI فقط کاتالوگ واقعی را می‌خواند؛ خطاها و سناریوهای شناسه با APIهای Mock در مرورگر تست می‌شوند.
گزارش و تصاویر در `test-artifacts/catalog/` ذخیره می‌شوند و وارد Git نمی‌شوند. هیچ تستی پیامک ارسال نمی‌کند.

بررسی محلی این مرحله در ۱۴ اکتبر ۲۰۲۶: ۴۹۸ محصول واجد شرایط، ۴۹۸ Match با Fragrantica و صفر مورد بدون Match.
برای هر ۴۹۸ محصول، URL معتبر تصویر اصلی موجود بود. حجم JSON حدود ۲۳۷ KiB و پاسخ GZip حدود ۳۹ KiB بود. اولین درخواست HTTP روی Loopback پس از راه‌اندازی حدود ۲۷۸ میلی‌ثانیه و میانهٔ ۲۰ Cache Hit حدود ۱۲ میلی‌ثانیه اندازه‌گیری شد؛ این اعداد Benchmark همین محیط هستند.

## انتخاب واقعی سفارش و لینک پیامک

Worker توکن قطعی و غیرترتیبی مبتنی بر HMAC را می‌سازد و لینکی مانند `https://your-domain.example/aB7xQ2mN9pR4tY6kL8` را پیامک می‌کند؛ توکن این لینک صرفاً مثال است. مقدار `TESTER_SELECTION_BASE_URL` باید فقط Origin، مانند `https://your-domain.example` باشد؛ مسیر اضافی، Query و Fragment پذیرفته نمی‌شوند و اسلش انتهایی نرمال می‌شود. مسیر `/{token}` فقط برای توکن دقیقاً ۱۸ کاراکتری Base64 URL-safe با حروف `a-z`، `A-Z`، اعداد `0-9` و دو کاراکتر `-` و `_` همان HTML ثابت را سرو می‌کند؛ CSS/JS و لوگو مسیر مطلق دارند و Refresh نیز کار می‌کند. HTML شامل اطلاعات مشتری یا توکن تعبیه‌شده نیست. مسیر قدیمی با پیشوند حذف شده است. `/health`، `/api/`، `/frontend/`، `/docs` و `/openapi.json` مستقل‌اند و مسیر توکن آن‌ها را نمی‌گیرد؛ مسیر ریشهٔ نامعتبر `404` می‌گیرد.

```dotenv
TESTER_SELECTION_BASE_URL=https://your-domain.example
```

API توکن را دوباره تولید نمی‌کند: فرمت `[A-Za-z0-9_-]{18}` بررسی، SHA256 رشتهٔ کامل و دقیق محاسبه و Selection با `token_hash` پیدا می‌شود. توکن همیشه String و Case-Sensitive است؛ هیچ‌جا Lowercase، Uppercase، Trim یا تبدیل عددی روی آن انجام نمی‌شود. تغییر حتی یک حرف از کوچک به بزرگ Hash متفاوتی تولید می‌کند و لینک سفارش اصلی را باز نمی‌کند؛ Lookup دیتابیس هم Collation از نوع `simple` دارد. عدد JSON، ارقام فارسی/عربی، `+`، `/`، `=`، فاصله و طول متفاوت رد می‌شوند. ارتباط سفارش فقط با `order_id` از نوع اصلی `ObjectId` است. `id` مانند `OR0000087510` فقط برای نمایش استفاده می‌شود. نسخه و وضعیت Selection و وجود سفارش بررسی می‌شوند. فیلتر محصول/تاریخ دقیقاً از Worker استفاده می‌شود و فقط شرط `sms_sent` حذف می‌شود. Cutoff، Secret، پرداخت، Notification و Retry پیامک تغییر نکرده‌اند. API برای سفارش تاریخی Selection ایجاد نمی‌کند.

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
  "selection": {"status": "pending", "selected_pids": [], "selected_names": []}
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
| `404` | Selection/سفارش ناموجود، خارج از Cutoff یا لینک نامعتبر |
| `409` | انتخاب متفاوت پس از Intent، فیلد قبلی سفارش، محصول ناموجود یا داده ناسازگار |
| `413` | Body بزرگ‌تر از ۸ KiB |
| `422` | تعداد نامعتبر، PID تکراری یا PID با نوع/فرمت نامعتبر |
| `429` | درخواست‌های بیش از حد؛ همراه `Retry-After` |
| `503` | خطای موقت MongoDB یا تنظیمات ناقص سرویس |

خطا به شکل `{"detail":{"code":"...","message":"پیام فارسی"}}` است و دادهٔ مشتری، ورودی توکن یا خطای خام دیتابیس را شامل نمی‌شود. موجودبودن `recommended_products` حتی با مقدار `null`، آرایهٔ خالی یا نام‌های یکسان مانع بازنویسی است. سفارش دارای فیلد قدیمی بدون شناسهٔ ثبت این سیستم، خطای کنترل‌شدهٔ `already_selected` می‌گیرد و خودکار مهاجرت یا تعمیر نمی‌شود.

### ثبت اتمیک، Idempotency و Recovery

سه وضعیت در همان `tester_selections` استفاده می‌شوند:

| وضعیت | معنی |
| --- | --- |
| `pending` | هنوز Intent ثبت نشده؛ انتخاب‌های صفحه قابل تغییرند |
| `submitting` | یک Intent معتبر، ثابت و قابل ادامه ثبت شده است |
| `submitted` | هر دو سند سازگارند و انتخاب‌ها برای همیشه فقط‌خواندنی‌اند |

بعد از اعتبارسنجی تازه، عملیات شرطی اتمیک `pending → submitting` فقط به یک درخواست اجازهٔ ثبت Intent می‌دهد. Intent شامل `submission_id`، ۱۰ `selected_pids`، ۱۰ `selected_names` و زمان‌هاست؛ درخواست دیگر نمی‌تواند آن را جایگزین کند. همین Intent نقطهٔ پذیرش انتخاب‌های اعتبارسنجی‌شده است و در Recovery همان snapshot ادامه پیدا می‌کند؛ موجودی رزرو یا کم نمی‌شود.

سپس سفارش فقط با `_id` واقعی، همان شرایط محصول/تاریخ، و شرط `recommended_products: {$exists: false}` و نبود Marker نوشته می‌شود. در یک Update اتمیک، فقط دو فیلد لازم اضافه می‌شوند:

```text
recommended_products: [10 exact source names]
tester_selection_submission_id: <same submission_id>
```

Marker برای تشخیص ثبت همین Intent از آرایهٔ قدیمیِ احتمالا یکسان لازم است. پس از تأیید تطابق Marker و تمام نام‌ها، Selection به `submitted` تغییر می‌کند. اطلاعات نهایی:

```text
order_id: ObjectId (unchanged)
token_hash / token_version / created_at: unchanged
status: submitted
submission_id: unique intent identifier
selected_pids: [10 strings in customer order]
selected_names: [10 exact products_api.name strings]
submitting_at / submitted_at / updated_at: UTC datetime
```

اگر قبل از نوشتن سفارش Crash رخ دهد، Context یا Submit همان Intent را ادامه می‌دهد؛ Lease و پاک‌سازی زمان‌دار لازم نیست. اگر نوشتن سفارش موفق شد ولی پاسخ آن یا نهایی‌کردن Selection از دست رفت، Marker و اسامی از دیتابیس خوانده می‌شوند و فقط Selection تکمیل می‌شود. Intent متفاوت `409` می‌گیرد. Retry همان ۱۰ PID با همان ترتیب، پاسخ موفق قبلی را بازمی‌گرداند. Intent و سفارش ناسازگار خودکار بازنویسی نمی‌شوند و نیاز به بررسی دارند.

این روش به Transaction وابسته نیست و روی Standalone نیز کار می‌کند. Write Concern همان `majority` فعلی Worker است. ایندکس‌های یکتای `order_id` و `token_hash` فعلی حفظ می‌شوند؛ Collection، Worker، Queue یا Migration جدیدی وجود ندارد. `ensure_selection` همچنان فقط `$setOnInsert` دارد و Selection ثبت‌شده را Reset نمی‌کند.

فرانت پیش از ثبت، فهرست ۱۰ نام را برای مرور نشان می‌دهد. هنگام ارسال، تغییر انتخاب‌ها و کلیک مجدد قفل می‌شوند. در خطای شبکه موفقیت حدس زده نمی‌شود؛ Context وضعیت واقعی را بررسی/بازیابی می‌کند. اگر آن هم در دسترس نبود، وضعیت نامشخص باقی می‌ماند و Retry فقط همان PIDهای ثابت را می‌فرستد. پس از موفقیت یا بازکردن دوباره لینک، رسید از نام‌های ذخیره‌شده نمایش داده می‌شود، حتی اگر محصول بعداً ناموجود باشد؛ کاتالوگ زنده برای نمایش رسید لازم نیست.

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

تنظیمات جدید فقط `TESTER_SELECTION_RATE_LIMIT_PER_MINUTE=60` است. `MONGODB_*`، `TESTER_ORDERS_COLLECTION`، `TESTER_PACK_PRODUCT_ID` و `TESTER_SMS_START_AT` موجود برای API نیز استفاده می‌شوند. برای نصب صرفاً کاتالوگ بدون تنظیمات معتبر Worker، برنامه بالا می‌آید ولی Context/Submit با `503` غیرفعال‌اند. پیکربندی فراز و `TESTER_SMS_ENABLED` فقط ارسال Worker را کنترل می‌کنند.

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
این تست‌ها شامل Worker تا ثبت نهایی، موجودی تازه، سقف ۴۹۸، ثبت هم‌زمان، عدم بازنویسی، قطع کلاینت واقعی MongoDB، Crash و بازیابی هر دو سند هستند. روی سفارش‌های واقعی هیچ تست ثبت انجام ندهید.

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

## سرویس پیامک پترن فرازSMS

سرویس `send_pattern` از [API پترن فرازSMS](https://docs.farazsms.com/service/post-ws-v1-sms-pattern) استفاده می‌کند.
ابتدا پترن را در پنل ایجاد و تأیید کنید؛ `FARAZ_PATTERN_CODE` همان UID پترن است.
این متغیرها را به `.env` موجود اضافه کنید؛ تنظیمات MongoDB خود را حفظ کنید:

```dotenv
FARAZ_API_KEY=your-api-key
FARAZ_API_URL=https://api.iranpayamak.com
FARAZ_LINE_NUMBER=your-sender-line
FARAZ_TIMEOUT_SECONDS=15
FARAZ_PATTERN_CODE=your-approved-pattern-code
```

تنظیمات با همان `python-dotenv`، از `.env` ریشه و با اولویت متغیرهای محیط خوانده می‌شوند.
تنظیمات فراز برای راه‌اندازی FastAPI الزامی نیستند؛ ساخت Payload به کد پترن و شمارهٔ خط و ارسال به کلید API نیز نیاز دارد.
نمونهٔ ساخت Payload بدون ارسال:

```python
from app.services.sms import FarazSmsClient

client = FarazSmsClient()
attributes = {"link": "https://example.com/token"}
payload = client.build_pattern_payload(recipient="09120000000", attributes=attributes)

# برای ارسال واقعی در کد مصرف‌کننده:
# result = client.send_pattern(recipient="09120000000", attributes=attributes)
# result.status_code, result.data
```

نام `link` صرفاً مثال است؛ کلیدهای `attributes` باید با نام متغیرهای پترن شما یکی باشند.
مقادیر رشته‌ای (حتی رشتهٔ خالی) و عددی مجازند؛ برای پترن بدون متغیر از `{}` استفاده کنید.
هر درخواست فقط یک `recipient` می‌پذیرد؛ شماره‌های `09...`، `989...`، `+989...` و ارقام فارسی نرمال می‌شوند.
نام متغیرها، نوع مقادیر، کد پترن و موبایل پیش از ارسال اعتبارسنجی می‌شوند؛ تطابق با متغیرهای پترن در پنل را API بررسی می‌کند.

متد `send_pattern` همزمان است؛ در کد `async` از `await run_in_threadpool(client.send_pattern, ...)`
با import از `starlette.concurrency` استفاده کنید. خطاهای سرویس از نوع `SmsServiceError` هستند.
سرویس هیچ متن، شماره، کلید، توکن لینک یا پاسخ خامی را لاگ نمی‌کند؛ `result.body` و `error.body` را نیز در کد مصرف‌کننده لاگ نکنید.
هر فراخوانی کلاینت فقط یک درخواست ارسال می‌کند؛ Retry توسط Worker مدیریت می‌شود.
طبق [OpenAPI رسمی](https://docs.farazsms.com/download/openapi.json)، پاسخ موفق `201` شامل `status="success"`، `data` عددی و `messages` است.
مقدار `data` بدون تعبیر به شناسهٔ درخواست در `result.data` و بدنهٔ کامل در `result.body` نگهداری می‌شود؛ ویژگی `request_id` وجود ندارد.
خطاهای HTTP، اتصال، `status="error"` و پاسخ نامعتبر با `SmsProviderError` گزارش می‌شوند.

اجرای تست‌های Payload، تنظیمات و پاسخ Mock؛ تمام درخواست‌های HTTP در تست‌ها مسدود یا Mock می‌شوند:

```shell
python -m unittest discover -s tests -v
```

## Worker سفارش‌های پک تستر

Worker مستقل، هر بار تعداد محدودی سفارش را از همان MongoDB پروژه بررسی می‌کند و سپس خارج می‌شود.
ساختار سفارش بر اساس نمونهٔ ارائه‌شده است: `product_id` آرایه، `date_created` رشتهٔ ISO با UTC و پسوند `Z`، و موبایل در `phone`.
شناسهٔ محصول نمونه استفاده نشده است؛ شناسهٔ واقعی پک تستر را در `TESTER_PACK_PRODUCT_ID` قرار دهید.
شناسهٔ عددی بدون صفر ابتدایی، هم به‌صورت String و هم Integer در عناصر آرایه تطبیق داده می‌شود؛ مقایسهٔ زیررشته یا رشته‌سازی کل آرایه انجام نمی‌شود.

سفارش باید محصول تستر را داشته باشد، تاریخش از شروع تعیین‌شده به بعد باشد و `sms_sent=true` نباشد.
سفارش جدید، Notification با وضعیت `prepared` یا `retry_pending` با موعد رسیده و تعداد تلاش کمتر از سقف، انتخاب می‌شود.
Notificationهای `accepted`، `exhausted` و خطای دائمی داده دوباره ارسال نمی‌شوند. عقب‌افتادگی اجرا با همین فیلتر ثابت تاریخ شروع پوشش داده می‌شود.
سفارش‌های دارای تاریخ نامعتبر، نوع متفاوت یا Timezone غیر از قالب UTC با `Z` انتخاب نمی‌شوند؛ هیچ Migration روی داده‌ها انجام نمی‌شود.

### تنظیمات Worker

| متغیر | کاربرد |
| --- | --- |
| `TESTER_ORDERS_COLLECTION` | Collection موجود سفارش‌ها؛ پیش‌فرض `preprocess_orders` |
| `TESTER_PACK_PRODUCT_ID` | شناسهٔ واقعی محصول پک تستر؛ الزامی |
| `TESTER_SMS_START_AT` | تاریخ شروع inclusive با Timezone؛ الزامی |
| `TESTER_WORKER_BATCH_SIZE` | حداکثر سفارش‌های ارسال در هر اجرا؛ پیش‌فرض `50` |
| `TESTER_SMS_ENABLED` | فقط `true` یا `false`؛ پیش‌فرض `false`، حالت فقط خواندنی |
| `TESTER_SELECTION_BASE_URL` | فقط Origin مانند `https://tester.example.com`، بدون مسیر؛ هنگام ارسال الزامی |
| `TESTER_PATTERN_LINK_ATTRIBUTE` | نام دقیق متغیر لینک در پترن؛ پیش‌فرض `link` |
| `TESTER_TOKEN_SECRET` | Secret تصادفی و ثابت با حداقل ۳۲ بایت؛ هنگام ارسال الزامی |
| `TESTER_SMS_RETRY_DELAY_MINUTES` | فاصلهٔ Retry به دقیقه؛ فقط عدد صحیح مثبت، پیش‌فرض `5` |
| `TESTER_SMS_MAX_ATTEMPTS` | سقف کل تلاش‌ها، شامل ارسال اول؛ فقط عدد صحیح مثبت، پیش‌فرض `3` |

تنظیمات `MONGODB_*` و `FARAZ_*` همان تنظیمات موجود پروژه هستند. برای ارسال، API Key، خط و کد پترن فراز باید تنظیم شده باشند.
برای Dry Run و حالت غیرفعال، تنظیمات لینک، Secret و اعتبارنامهٔ پیامک ضروری نیستند.
Secret را یک بار مثلاً با `python -c "import secrets; print(secrets.token_urlsafe(48))"` تولید و در `.env` قرار دهید؛ آن را ثابت نگه دارید.
توکن Base64 URL-safe نسخهٔ ۳ از HMAC-SHA256 با همان Secret ثابت، شناسهٔ دارای نوع سفارش و نسخه ساخته می‌شود: خروجی HMAC با `urlsafe_b64encode` کدگذاری می‌شود و ۱۸ کاراکتر اول با همان حروف اصلی نگهداری می‌شوند. در این بخش ابتدایی Padding وجود ندارد. تبدیل عددی و پیمانه‌گیری حذف شده‌اند؛ شمارهٔ نمایشی سفارش و شمارنده مبنای توکن نیستند. بزرگی و کوچکی حروف و علائم در لینک، JavaScript، Body و Hash حفظ می‌شوند. همهٔ Retryهای یک سفارش با Secret و نسخهٔ ثابت دقیقاً همان لینک را می‌فرستند.

Worker در `tester_selections` فقط Hash توکن، شناسهٔ سفارش، نسخه، وضعیت اولیه و زمان ایجاد را درج می‌کند؛ API ثبت نهایی، اطلاعات Intent و انتخاب‌ها را به همان سند اضافه می‌کند. فضای توکن محدود است؛ ایندکس یکتای `token_hash` مانع تخصیص یک توکن به دو سفارش می‌شود. در برخورد نادر توکن، Worker بدون ارسال پیامک برای سفارش دوم با خطای غیرحساس متوقف می‌شود و توکن سفارش اول را دوباره تخصیص نمی‌دهد.

ناسازگاری Secret یا نسخه با Selection موجود، خطای سراسری تنظیمات است؛ Worker پیش از ارسال آن سفارش متوقف می‌شود و Notification را به `failed` دائمی تغییر نمی‌دهد. برای ناسازگاری Secret، مقدار اصلی را بازگردانید. رکوردهای نسخهٔ قبلی خودکار تبدیل یا بازنویسی نمی‌شوند؛ هیچ Migration روی داده‌های موجود انجام نمی‌شود.

تاریخ پیش‌فرض `2026-10-14T00:00:00+03:30` برابر با `2026-10-13T20:30:00Z` است.
ابتدا تاریخ Environment به UTC تبدیل می‌شود. فیلتر اولیهٔ ایندکس‌شده بر اساس ثانیه است؛ سپس بخش اعشاری تاریخ در MongoDB به طول ثابت ۹ رقم تبدیل و مرز دقیق کنترل می‌شود.
بنابراین اعشار متغیر نمونه، مثل `.83444Z` و `.729294Z`، یا نبود اعشار، باعث مقایسهٔ اشتباه نمی‌شود؛ تقویم نیز بدون بخش اعشاری اعتبارسنجی می‌شود.
این تبدیل فقط در Query انجام می‌شود و تاریخ ذخیره‌شده را تغییر نمی‌دهد.

### ایندکس‌ها و اجرای یک نوبت

پس از تنظیم Environment، ایندکس‌ها را یک بار با فرمان مستقل ایجاد کنید؛ اجرای دوبارهٔ این فرمان ایندکس‌های موجود را بررسی/بازاستفاده می‌کند:

```shell
python -m app.workers.tester_sms --create-indexes
```

این فرمان روی سفارش‌ها ایندکس ترکیبی `(product_id, date_created, _id)` با Collation ساده ایجاد می‌کند.
در `tester_sms_notifications` ایندکس یکتای `order_id` و ایندکس‌های بازیابی/اصلاح/Retry، و در `tester_selections` ایندکس‌های یکتای `order_id` و `token_hash` ایجاد می‌شوند.
پس از ارتقای Worker، همین فرمان را دوباره اجرا کنید تا ایندکس جدید `(status, next_retry_at)` اضافه شود.
هیچ ایندکسی هنگام Import ساخته نمی‌شود. Query سفارش‌ها با Hint ایندکس ترکیبی اجرا می‌شود؛ در نبود آن با خطا متوقف می‌شود و به Full Collection Scan برنمی‌گردد.
فیلتر و Join با Notification در MongoDB انجام می‌شوند؛ Limit بعد از حذف وضعیت‌های غیرقابل ارسال و Retryهای زودتر از موعد اعمال می‌شود.
Cursor مرحله‌ای خوانده می‌شود، `skip` استفاده نمی‌شود و پردازش همهٔ سفارش‌ها در حافظه انجام نمی‌شود.

Dry Run فقط خلاصهٔ تعداد سفارش‌های واجد شرایط تا سقف Limit را نمایش می‌دهد؛ توکن، Selection، Notification، ایندکس یا پیامک ایجاد نمی‌کند:

```shell
python -m app.workers.tester_sms --dry-run --limit 10
```

اجرای دستی یک نوبت:

```shell
python -m app.workers.tester_sms --once --limit 50
```

`--limit` سقف پیش‌فرض Batch را جایگزین می‌کند. پیش از ارسال واقعی، `TESTER_SMS_ENABLED=true` را خودتان تنظیم کنید.
با مقدار `false`، حتی `--once` فقط خلاصهٔ خواندنی می‌دهد و هیچ وضعیتی به `sending` یا `accepted` تغییر نمی‌کند.
اصلاح سفارش‌های پذیرفته‌شده و بازیابی وضعیت‌های معلق، هر کدام نیز تا سقف Limit در همان اجرا انجام می‌شوند.
هیچ API یا Worker همیشگی راه‌اندازی نمی‌شود؛ در اجرای مستقل، اتصال AsyncMongoClient ایجاد و در پایان بسته می‌شود.

نمونهٔ Cron در Linux، هر دو دقیقه؛ مسیر پروژه را جایگزین کنید:

```cron
*/2 * * * * cd /srv/TesterPack_Page && /srv/TesterPack_Page/.venv/bin/python -m app.workers.tester_sms --once >> /srv/TesterPack_Page/tester-worker.log 2>&1
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

سازگاری رکوردهای قدیمی فقط برای سفارش‌های داخل محدودهٔ تاریخ/محصول، به‌صورت تدریجی و بدون Migration یا Bulk Update انجام می‌شود.
`submission_unknown` و `failed` قدیمی با کدی غیر از `invalid_order_data` قابل بازیابی‌اند؛ در نبود `attempt_count`، یک تلاش قبلی برای آن‌ها منظور می‌شود.
موعد آن‌ها از زمان آخرین تلاش/آپدیت به‌علاوهٔ فاصلهٔ Retry ساخته می‌شود؛ اگر زمان قبلی وجود نداشته باشد از زمان بازیابی محاسبه می‌شود.
اگر `error_code` یا `last_error_code` برابر `invalid_order_data` باشد، `failed` دائمی باقی می‌ماند؛ کد خطای جاری در هر دو فیلد یکسان ثبت می‌شود. سفارش‌های قبل از شروع در بازیابی نیز تغییر نمی‌کنند.
اگر سقف تنظیم‌شده کاهش یابد، Retryهای با تعداد تلاش بیشتر یا مساوی سقف به `exhausted` تبدیل می‌شوند.

اطلاعات تلاش‌ها در همان `tester_sms_notifications` نگهداری می‌شوند: `attempt_count`، `next_retry_at`، `last_attempt_at`، `last_error_code`، Lease، شناسهٔ تلاش و وضعیت پذیرش/همگام‌سازی.
آرایهٔ `attempts` شامل شماره/شناسهٔ تلاش، زمان شروع/پایان، نتیجه، HTTP Status و کد خطای غیرحساس است. موبایل، متن، لینک، توکن و بدنهٔ پاسخ در آن ذخیره نمی‌شوند.
تمام Retryها همان Selection و لینک HMAC قبلی را استفاده می‌کنند.

پس از موفقیت، فقط HTTP Status، `data` عددی و زمان پذیرش در Notification ثبت می‌شود؛ سپس `sms_sent=true` روی همان سفارش واجد شرایط نوشته می‌شود.
اگر مرحلهٔ دوم شکست بخورد، اجرای بعدی فقط فیلد سفارش را اصلاح می‌کند و پیامک دوباره نمی‌فرستد.
اگر پاسخ موفق قبل از ثبت `accepted` در MongoDB گم شود، پس از بازیابی Lease و موعد Retry ارسال دوباره مجاز است؛ `sms_sent` تا ثبت موفقیت قطعی تغییر نمی‌کند.
برای سفارش حذف‌شده یا خارج از محدودهٔ محصول/تاریخ، اصلاح انجام نمی‌شود و `order_sync_blocked` روی Notification ثبت می‌شود.
`sms_sent=true` یعنی پذیرش درخواست توسط فراز، نه تأیید تحویل به گوشی.
لاگ‌ها فقط خلاصه و پیام خطای عمومی دارند؛ شماره، Secret، API Key، توکن، لینک اختصاصی یا بدنهٔ پاسخ ثبت نمی‌شوند.

تعداد کل سفارش‌های `exhausted` در خلاصهٔ هر اجرا با `exhausted_total` گزارش می‌شود. گزارش خواندنی با شناسهٔ سفارش، تعداد تلاش و آخرین کد خطا:

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
python -m unittest tests.test_tester_sms_integration -v
```

در Linux:

```shell
TESTER_RUN_MONGO_INTEGRATION=1 python -m unittest tests.test_tester_sms_integration -v
```

قطع ارتباط با بستن واقعی کلاینت MongoDB و اتصال دوباره آزمایش می‌شود؛ سرویس MongoDB مشترک متوقف نمی‌شود.
Integration شامل فیلتر دقیق تاریخ/محصول، `$lookup`، ایندکس‌ها، Claim هم‌زمان، Retry، حفظ نتیجهٔ پذیرفته‌شده و عدم تغییر دیتابیس در Dry Run است.
خلاصهٔ Explain روی دادهٔ ساختگی در `test-artifacts/tester-worker-explain.json` ذخیره می‌شود؛ این پوشه وارد Git نمی‌شود.

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
      faraz.py    # ارسال پیامک پترن
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
