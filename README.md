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

## ساختار

```text
app/
  __init__.py
  config.py       # خواندن تنظیمات
  main.py         # برنامه، Lifespan و health
.env              # تنظیمات محلی؛ خارج از Git
.env.example
.gitignore
requirements.txt
README.md
```
