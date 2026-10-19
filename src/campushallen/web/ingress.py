"""Trust Cloudflare identity only from the configured tunnel peer, never from XFF."""

from ipaddress import ip_address

from fastapi import Request
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from campushallen.settings import Settings
from campushallen.web.i18n import gettext as _


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


class TrustedIngress:
    def __init__(self, app: ASGIApp, settings: Settings):
        self.app, self.settings = app, settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        peer = scope.get("client", ("unknown", 0))[0]
        if peer in self.settings.trusted_tunnel_ips:
            try:
                visitor = str(ip_address(headers.get(b"cf-connecting-ip", b"").decode("ascii")))
            except (ValueError, UnicodeDecodeError):
                await PlainTextResponse(_("Invalid request. Check the form and try again."), 400)(
                    scope, receive, send
                )
                return
            # Cloudflare sets this header at the edge. Require HTTPS for public ingress.
            if headers.get(b"x-forwarded-proto") != b"https":
                await PlainTextResponse(_("Use HTTPS to access this website."), 400)(
                    scope, receive, send
                )
                return
            scope = {**scope, "client": (visitor, 0), "scheme": "https"}
        if self.settings.app_env == "production" and scope.get("method") in (
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
        ):
            origin = headers.get(b"origin", b"").decode("latin-1")
            referer = headers.get(b"referer", b"").decode("latin-1")
            from urllib.parse import urlsplit

            source = urlsplit(origin or referer)
            expected = self.settings.public_base_url
            if f"{source.scheme}://{source.netloc}" != expected:
                await PlainTextResponse(
                    _("The form origin could not be verified. Reload the page and try again."), 403
                )(scope, receive, send)
                return
        await self.app(scope, receive, send)
