"""Private-page headers, bounded per-process throttling and access-log redaction."""

import logging
from collections import OrderedDict
from time import monotonic

from fastapi.responses import JSONResponse


PRIVATE_HEADERS = {
    "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow, noarchive", "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; connect-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' https:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'",
}


class RedactSelectionAccessLog(logging.Filter):
    def filter(self, record):
        # Uvicorn passes the request target as the third interpolation argument.
        if isinstance(record.args, tuple) and len(record.args) == 5:
            args = list(record.args)
            target = str(args[2])
            if target.split("?", 1)[0].lower().startswith("/s/"):
                args[2] = "/s/[redacted]"
            elif target.startswith("/api/tester/selection/"):
                args[2] = target.split("?", 1)[0]
            record.args = tuple(args)
        return True


def install_access_log_redaction():
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, RedactSelectionAccessLog) for item in access.filters):
        access.addFilter(RedactSelectionAccessLog())


class SelectionSecurity:
    def __init__(self, app, requests_per_minute=60):
        self.app = app
        self.limit = requests_per_minute
        self.clients = OrderedDict()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"]
        private = path.startswith("/s/") or path.startswith("/api/tester/selection/") or path in {"/", "/frontend/index.html"}
        if not private:
            return await self.app(scope, receive, send)

        async def private_send(message):
            if message["type"] == "http.response.start":
                message = dict(message)
                protected = {key.lower().encode() for key in PRIVATE_HEADERS}
                message["headers"] = [(key, value) for key, value in message.get("headers", []) if key.lower() not in protected]
                message["headers"] += [(key.lower().encode(), value.encode()) for key, value in PRIVATE_HEADERS.items()]
            await send(message)

        if path.startswith("/api/tester/selection/"):
            now = monotonic()
            key = (scope.get("client") or ("unknown",))[0]
            started, count = self.clients.pop(key, (now, 0))
            if now - started >= 60:
                started, count = now, 0
            self.clients[key] = (started, count + 1)
            while len(self.clients) > 2048:
                self.clients.popitem(last=False)
            if count >= self.limit:
                response = JSONResponse({"detail": {"code": "rate_limited", "message": "درخواست‌ها زیاد است. لطفاً یک دقیقه صبر کنید."}},
                                        status_code=429, headers={"Retry-After": str(max(1, int(60 - (now - started))))})
                return await response(scope, receive, private_send)
        await self.app(scope, receive, private_send)
