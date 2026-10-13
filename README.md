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

## ساختار

```text
app/
  __init__.py
  config.py       # خواندن تنظیمات
  main.py         # برنامه، Lifespan و health
  services/
    __init__.py
    sms/
      __init__.py
      faraz.py    # ارسال پیامک پترن
tests/
  __init__.py
  test_sms_faraz.py
.env              # تنظیمات محلی؛ خارج از Git
.env.example
.gitignore
requirements.txt
README.md
```
