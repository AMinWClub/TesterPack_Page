# پروژهٔ مینیمال FastAPI

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
دیتابیس برای توسعهٔ بعدی از `request.app.state.db` قابل دسترسی است؛ فعلاً هیچ Collection ساخته نمی‌شود.

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
هر فراخوانی فقط یک درخواست ارسال می‌کند و Retry خودکار ندارد.
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

سفارش باید محصول تستر را داشته باشد، تاریخش از شروع تعیین‌شده به بعد باشد، `sms_sent=true` نباشد و Notification ارسال‌شده یا نهایی‌شده نداشته باشد.
Notification در وضعیت `prepared` هنوز ارسال نشده و قابل ادامه است. عقب‌افتادگی اجرا با همین فیلتر ثابت تاریخ شروع پوشش داده می‌شود.
سفارش‌های دارای تاریخ نامعتبر، نوع متفاوت یا Timezone غیر از قالب UTC با `Z` انتخاب نمی‌شوند؛ هیچ Migration روی داده‌ها انجام نمی‌شود.

### تنظیمات Worker

| متغیر | کاربرد |
| --- | --- |
| `TESTER_ORDERS_COLLECTION` | Collection موجود سفارش‌ها؛ پیش‌فرض `preprocess_orders` |
| `TESTER_PACK_PRODUCT_ID` | شناسهٔ واقعی محصول پک تستر؛ الزامی |
| `TESTER_SMS_START_AT` | تاریخ شروع inclusive با Timezone؛ الزامی |
| `TESTER_WORKER_BATCH_SIZE` | حداکثر سفارش‌های ارسال در هر اجرا؛ پیش‌فرض `50` |
| `TESTER_SMS_ENABLED` | فقط `true` یا `false`؛ پیش‌فرض `false`، حالت فقط خواندنی |
| `TESTER_SELECTION_BASE_URL` | آدرس پایه شامل مسیر انتخاب، مانند `https://tester.example.com/s`؛ هنگام ارسال الزامی |
| `TESTER_PATTERN_LINK_ATTRIBUTE` | نام دقیق متغیر لینک در پترن؛ پیش‌فرض `link` |
| `TESTER_TOKEN_SECRET` | Secret تصادفی و ثابت با حداقل ۳۲ بایت؛ هنگام ارسال الزامی |

تنظیمات `MONGODB_*` و `FARAZ_*` همان تنظیمات موجود پروژه هستند. برای ارسال، API Key، خط و کد پترن فراز باید تنظیم شده باشند.
برای Dry Run و حالت غیرفعال، تنظیمات لینک، Secret و اعتبارنامهٔ پیامک ضروری نیستند.
Secret را یک بار مثلاً با `python -c "import secrets; print(secrets.token_urlsafe(48))"` تولید و در `.env` قرار دهید؛ آن را ثابت نگه دارید.
هر توکن ۲۴ کاراکتری از HMAC-SHA256 با شناسهٔ دارای نوع سفارش و نسخهٔ توکن ساخته می‌شود. در `tester_selections` فقط Hash توکن، شناسهٔ سفارش، نسخه، وضعیت و زمان ایجاد نگهداری می‌شود.
تغییر Secret بعد از ایجاد Selection باعث ناسازگاری توکن سفارش‌های آماده می‌شود؛ Worker آن سفارش را ارسال نمی‌کند.

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
در `tester_sms_notifications` ایندکس یکتای `order_id` و ایندکس‌های بازیابی/اصلاح، و در `tester_selections` ایندکس‌های یکتای `order_id` و `token_hash` ایجاد می‌شوند.
هیچ ایندکسی هنگام Import ساخته نمی‌شود. Query سفارش‌ها با Hint ایندکس ترکیبی اجرا می‌شود؛ در نبود آن با خطا متوقف می‌شود و به Full Collection Scan برنمی‌گردد.
فیلتر و Join با Notification در MongoDB انجام می‌شوند؛ Limit بعد از حذف سفارش‌های دارای درخواست قبلی اعمال می‌شود تا آن سفارش‌ها جلوی پردازش سفارش‌های بعدی را نگیرند.
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
| `accepted` | فراز درخواست را پذیرفته و پاسخ موفق به‌صورت پایدار ثبت شده است |
| `failed` | دادهٔ نامعتبر یا رد قطعی درخواست؛ ارسال مجدد خودکار ندارد |
| `submission_unknown` | نتیجهٔ ارسال قابل اثبات نیست؛ ارسال مجدد خودکار ممنوع است |

Claim با تغییر شرطی `prepared` به `sending` در یک سند انجام می‌شود؛ [MongoDB این تغییر تک‌سندی را اتمیک اجرا می‌کند](https://www.mongodb.com/docs/manual/core/write-operations-atomicity/).
نوشتن‌ها با Write Concern برابر `majority` انجام می‌شوند و فراخوانی فراز فقط بعد از تأیید Claim انجام می‌شود.
اگر Worker بعد از Claim قطع شود، سفارش بلافاصله از ارسال‌های بعدی حذف می‌شود؛ پس از گذشت Timeout فراز به‌علاوهٔ ۵ دقیقه، اجرای بعدی رکورد را `submission_unknown` می‌کند.
انقضای Claim فقط برای تشخیص وضعیت نامشخص است و هرگز اجازهٔ ارسال مجدد نمی‌دهد.
این روش از تکرار خودکار جلوگیری می‌کند؛ قطع‌شدن بین Claim و ارسال نیز می‌تواند سفارش را نیازمند بررسی دستی کند.

خطاهای HTTP از خانوادهٔ `4xx`، به‌جز `408`، و پاسخ صریح `status="error"` در HTTP موفق، رد قطعی محسوب می‌شوند.
Timeout، `408`، `5xx`، قطع اتصال، پاسخ نامعتبر یا خطای غیرمنتظرهٔ ارسال، نتیجهٔ نامشخص دارند.
موبایل نامعتبر یا دادهٔ ناقص، Notification خطا می‌گیرد و پردازش سایر سفارش‌ها ادامه پیدا می‌کند.
در خطا و وضعیت نامشخص، بررسی پنل/پشتیبانی فراز و تصمیم دستی لازم است؛ هیچ Retry یا Reconciliation ارسال در این Worker وجود ندارد.
رکوردهای مبهم را بدون بررسی نتیجه به `prepared` برنگردانید؛ ممکن است درخواست قبلاً پذیرفته شده باشد.

پس از موفقیت، فقط HTTP Status، `data` عددی و زمان پذیرش در Notification ثبت می‌شود؛ سپس `sms_sent=true` روی همان سفارش واجد شرایط نوشته می‌شود.
اگر مرحلهٔ دوم شکست بخورد، اجرای بعدی فقط فیلد سفارش را اصلاح می‌کند و پیامک دوباره نمی‌فرستد.
برای سفارش حذف‌شده یا خارج از محدودهٔ محصول/تاریخ، اصلاح انجام نمی‌شود و `order_sync_blocked` روی Notification ثبت می‌شود.
`sms_sent=true` یعنی پذیرش درخواست توسط فراز، نه تأیید تحویل به گوشی.
لاگ‌ها فقط خلاصه و پیام خطای عمومی دارند؛ شماره، Secret، API Key، توکن، لینک اختصاصی یا بدنهٔ پاسخ ثبت نمی‌شوند.

تست‌های Worker و سرویس پیامک با MongoDB و HTTP Mock اجرا می‌شوند:

```shell
python -m unittest discover -s tests -v
```

## ساختار

```text
app/
  __init__.py
  config.py       # خواندن تنظیمات
  main.py         # برنامه، Lifespan و health
  workers/
    __init__.py
    tester_sms.py # Worker مستقل و یک‌نوبتی
  services/
    __init__.py
    sms/
      __init__.py
      faraz.py    # ارسال پیامک پترن
tests/
  __init__.py
  test_sms_faraz.py
  test_tester_sms_worker.py
.env              # تنظیمات محلی؛ خارج از Git
.env.example
.gitignore
requirements.txt
README.md
```
