"""Bound form bodies before parsing, including requests without Content-Length."""

from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from campushallen.web.i18n import gettext as _


class FormBodyLimit:
    def __init__(self, app: ASGIApp, limit: int = 262144):
        self.app, self.limit = app, limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in ("POST", "PUT", "PATCH"):
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            declared = self.limit + 1
        if declared > self.limit or declared < 0:
            await PlainTextResponse(_("Request body too large."), status_code=413)(
                scope, receive, send
            )
            return
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.limit:
                await PlainTextResponse(_("Request body too large."), status_code=413)(
                    scope, receive, send
                )
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        body, sent = b"".join(chunks), False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)
