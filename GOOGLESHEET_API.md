# Google Sheets API inside TesterPack

The tester FastAPI application hosts this module; run `uvicorn app.main:app --reload` from the tester project. No separate API process is needed.

- Report: `POST /googlesheetapi/` with `X-API-Key`.
- Health: `GET /googlesheetapi/health`.
- Compatibility path: `POST /googlesheetapi/api/v1/reports/utm-daily-sales`.
- Google Apps Script: copy `google_apps_script/Code.gs` and set Script Properties `SALES_API_URL=https://YOUR_TESTER_DOMAIN/googlesheetapi/` and `SALES_API_KEY` to the server key. Update the existing Script Property when migrating.
- Settings come from the tester root `.env`. `GOOGLESHEET_MONGO_URI` and `GOOGLESHEET_MONGO_DB` are optional overrides; otherwise the tester URI and database selected by `APP_ENV` apply. `GOOGLESHEET_MONGO_COLLECTION` and `SALES_API_KEY` are required for reports. Missing report settings do not prevent the tester from starting.
- The local migration preserves the original report connection in the ignored `.env`. For deployment, configure these values on the server; credentials are not included in Git.
- The report uses its own synchronous MongoDB client in FastAPI's thread pool; clients close with the tester lifespan. All report queries are read-only.
- Install `requirements.txt` (including `tzdata` for Windows). For tests install `requirements-dev.txt`, then run `python -m pytest tests/test_googlesheet_report.py` and `node tests/test_code_gs.cjs`.

The operational details below describe the report, request contract, and Google Apps Script setup.

# گزارش خودکار فروش روزانه UTM → Google Sheets

پیاده‌سازی کامل **FastAPI + MongoDB + Google Apps Script** بر اساس ساختار فایل `black.xlsx`.
این پروژه خودش به سرور یا حساب گوگل شما متصل نشده است؛ پس از تنظیم دسترسی‌ها، اجرا و نصب Trigger اتوماتیک می‌شود.

## قواعد محاسبه

- شروع گزارش: **۱۴۰۵/۰۸/۲۵ = 2026-11-16**، روز به روز تا امروز در **Asia/Tehran**.
- تاریخ پرداخت `date_paid` مبنای روز فروش است، **نه** `date_created`.
- `date_paid` در دیتابیس نمونه یک **رشته UTC با ISO-8601** است؛ کوئری با همین قالب طراحی شده.
- سفارش‌هایی که **`state == "cancelled"`** هستند، حذف می‌شوند. فیلد `status` عمداً وارد شرط نمی‌شود.
- مبلغ فروش = جمع `total` هر سفارش، بدون ضرب در تعداد محصولات، بدون کسر ارسال و تخفیف اضافه؛ دقیقاً مقدار ذخیره‌شده در سند.
- فیلترهای `source`/`medium`/`campaign` در شیت به ترتیب فیلدهای `utm_source`/`utm_medium`/`utm_campaign` در MongoDB می‌روند.
- `source` و `medium` هر دو باید مقدار داشته باشند؛ اگر هر کدام خالی باشد، ردیف پردازش نمی‌شود و داده قبلی خروجی آن خالی می‌شود.
- `campaign` خالی: **هیچ شرطی** روی `utm_campaign` اعمال نمی‌شود (مجموع همه کمپین‌های source/medium).
- مقایسه UTMها نسبت به حروف بزرگ و کوچک غیرحساس است؛ همچنین ارقام فارسی/عربی به لاتین و نیم‌فاصله و کاراکترهای نامرئی حذف می‌شوند. اسناد اصلی MongoDB تغییری نمی‌کنند.
- در فایل `black.xlsx`: ردیف ۱ هدر، ردیف ۲ اولین داده، ردیف **۳ (کنجد) کلاً مستثنا**، ستون G=source، H=medium، I=campaign، ستون **K به بعد** روزهای شمسی. A:J دست‌نخورده.
- مقادیر بدون فروش **۰** می‌شوند. دو ردیف با UTM یکسان (مثل ۲۲ و ۲۵) همان خروجی یکسان را می‌گیرند؛ جمع ردیف‌های شیت لزوماً بدون هم‌پوشانی نیست.
- واحد مبلغ دقیقاً واحد `total` در دیتابیس است. تبدیل ریال ↔ تومان انجام نمی‌شود.

## نصب روی سرور (Python 3.10+)

```bash
cd TesterPack_Page
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

مقادیر `.env` را تنظیم کنید:

```dotenv
GOOGLESHEET_MONGO_URI=mongodb://127.0.0.1:27017/
GOOGLESHEET_MONGO_DB=YOUR_DATABASE
GOOGLESHEET_MONGO_COLLECTION=YOUR_ORDER_COLLECTION
SALES_API_KEY=GENERATE_A_LONG_SECRET
START_DATE=2026-11-16
MONGO_MAX_TIME_MS=60000
```

برای ساخت کلید تصادفی:

```bash
python -c 'import secrets; print(secrets.token_urlsafe(40))'
```

سپس API را محلی اجرا کنید:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

تست سلامت (نیازی به اتصال MongoDB ندارد):

```bash
curl http://127.0.0.1:8000/googlesheetapi/health
```

نمونه درخواست از خود سرور:

```bash
curl -X POST http://127.0.0.1:8000/googlesheetapi/ \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: YOUR_SECRET' \
  -d '{"rows":[{"row":2,"source":"bale","medium":"daily","campaign":null},{"row":4,"source":"landing","medium":"home","campaign":"bf"}],"end_date":"2026-11-17"}'
```

> `end_date` اختیاری است و فقط برای تست یا محاسبه تا یک روز مشخص استفاده می‌شود؛ در اجرای Google Sheets ارسال نمی‌شود و API تا امروز ایران حساب می‌کند.

شکل پاسخ:

```json
{
  "start_date": "2026-11-16",
  "end_date": "2026-11-17",
  "timezone": "Asia/Tehran",
  "days": [
    {"date": "2026-11-16", "jalali": "1405/08/25"},
    {"date": "2026-11-17", "jalali": "1405/08/26"}
  ],
  "rows": [
    {"row": 2, "totals": ["0", "1200000"]},
    {"row": 4, "totals": ["2000000", "0"]}
  ]
}
```

اعداد بالا **مثالی هستند**.

## HTTPS برای Google Apps Script

اسکریپت گوگل باید بتواند به API از اینترنت عمومی با HTTPS معتبر متصل شود؛ `127.0.0.1` یا IP داخلی سرور به تنهایی کافی نیست.

مثال ساده با Caddy روی دامنه خودتان (دامنه نمونه را جایگزین کنید):

```caddyfile
sales-api.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

فقط Caddy روی پورت 443 عمومی باشد و uvicorn روی 127.0.0.1 باقی بماند. یک کلید `SALES_API_KEY` طولانی و غیرقابل حدس بگذارید. به هیچ عنوان URI دیتابیس را داخل Google Apps Script یا شیت قرار ندهید.

## فعال‌کردن داخل Google Sheets

1. فایل اکسل را در Google Drive به **Google Sheets** تبدیل کنید (صرفاً آپلود Excel کافی نیست). اطمینان حاصل کنید عنوان تب `Sheet1` است یا آن را در ابتدای `Code.gs` تغییر دهید.
2. در Google Sheets از **Extensions → Apps Script** وارد شوید و تمام فایل `google_apps_script/Code.gs` را در `Code.gs` جای‌گذاری کنید.
3. از **Project Settings → Script Properties** دو Property بسازید:
   - `SALES_API_URL` با مقدار کامل `https://sales-api.example.com/googlesheetapi/`
   - `SALES_API_KEY` با همان کلید `.env` سرور
4. اسکریپت را ذخیره کنید؛ یک بار تابع `syncDailySales` را در ویرایشگر اجرا و مجوزهای Google Sheets و درخواست HTTP را تأیید کنید. اجرای اول، `SALES_SPREADSHEET_ID` را به‌طور خودکار در Script Properties ذخیره می‌کند تا Trigger ساعتی حتی وقتی شیت باز نیست کار کند. اگر اسکریپت را مستقل از شیت اجرا می‌کنید، خودتان `SALES_SPREADSHEET_ID` را هم از URL شیت وارد کنید.
5. شیت را Refresh کنید. منوی **گزارش فروش UTM** اضافه می‌شود. «به‌روزرسانی فروش» اجرای دستی و «فعال‌کردن آپدیت ساعتی» ساخت Trigger ساعتی است. نصب مجدد Trigger قبلی همان پروژه را جایگزین می‌کند.

**اسکریپت چه تغییراتی می‌دهد؟** فقط خانه‌های تاریخ شمسی در سطر ۱ از ستون K و مقادیر فروش در سطرهای معتبر از K به بعد را می‌نویسد. A:J را نمی‌نویسد، و سطر ۳ را در خروجی هم تغییر نمی‌دهد. همه روزهای گذشته در هر اجرا مجدداً محاسبه می‌شوند تا لغو شدن سفارش در روزهای قبلی نیز اصلاح شود.

## کارایی / محدودیت مهم

- MongoDB در **یک Aggregation** سفارش‌ها را بر اساس روز تهران و رشته‌های خام UTM جمع می‌کند. سرویس پایتون مقدارهای متفاوت از لحاظ بزرگی/کوچکی و نیم‌فاصله را با هم ادغام می‌کند و نتیجه را روی فیلترهای شیت اعمال می‌کند. از اجرای یک کوئری به ازای هر ردیف جلوگیری می‌شود.
- چون `date_paid` در دیتابیس رشته ISO UTC است، کوئری از محدوده رشته‌ای روی `date_paid` استفاده می‌کند. برای جلوگیری از اسکن تمام کالکشن، **وجود ایندکس روی `date_paid` را بررسی کنید**. اگر وجود ندارد و با سیاست ایندکس‌های پروژه شما سازگار است، در mongosh اجرا کنید:

```javascript
// ابتدا با getIndexes() وجود ایندکس مشابه را چک کنید.
db.getCollection('YOUR_ORDER_COLLECTION').getIndexes()
db.getCollection('YOUR_ORDER_COLLECTION').createIndex({ date_paid: 1 })
```

- به دلیل تبدیل `date_paid` به تاریخ و نرمال‌سازی رشته‌ها، **این نسخه برای قالب نمونه شما (UTC ISO string)** است؛ اگر نوع `date_paid` در کالکشن ترکیبی از BSON Date و string است، باید پایپ‌لاین قبل از اجرا تطبیق داده شود.
- `MONGO_MAX_TIME_MS` از کوئری سنگین بی‌انتها جلوگیری می‌کند؛ اگر API خطای 503 بدهد، اسکریپت شیت را به‌روزرسانی نمی‌کند و داده قبلی باقی می‌ماند.
- `total` در Aggregation با Decimal محاسبه و در JSON به‌صورت string منتقل می‌شود تا حین JSON precision از دست نرود. Google Sheets آن را به Number تبدیل می‌کند و اگر بیشتر از محدوده امن Number جاوااسکریپت باشد، خطا می‌دهد؛ در مبالغ معمول گزارش فروش نباید مشکل باشد.
- اگر یک سفارش منطبق `date_paid` یا `total` نامعتبر داشته باشد، API به‌جای گزارش ناقص خطا می‌دهد و شیت تغییر نمی‌کند.
- برای طول عمر گزارش ۱۰ سال سقف گذاشته شده؛ با افزایش تاریخ در آینده می‌توان این محدودیت را بازنگری کرد.
- اگر واقعاً می‌خواهید داده‌های یک کمپین را بر اساس **تاریخ شروع آن ردیف** محدود کنید، این نیاز جداگانه‌ای است؛ نسخه فعلی طبق توافق، همه ردیف‌ها را از ۲۵ آبان محاسبه می‌کند، نه از تاریخ ثبت‌شده در ستون A/J.

## تست

```bash
pip install -r requirements-dev.txt
pytest -q
# optional JS mock test — requires Node.js, no npm install:
node tests/test_code_gs.cjs
```

تست‌های واحد شامل تاریخ‌های شمسی، مرزهای زمانی تهران، `state` در برابر `status`، یک‌بار بودن aggregation، کمپین اختیاری، ارقام فارسی/نیم‌فاصله، و ردیف‌های تکراری است. قبل از استفاده عملی، پاسخ چند روز از API را با یک aggregation دستی روی دیتابیس خودتان تطبیق دهید.
