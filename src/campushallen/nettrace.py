from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


SENSITIVE_HEADERS = {
    "authorization",
    "cookie",
    "set-cookie",
    "x-csrf-token",
    "x-xsrf-token",
    "x-auth-token",
}
SENSITIVE_QUERY_PARTS = ("token", "code", "password", "session", "auth", "key")
MAX_BODY_CHARS = 20_000


def sanitize_url(url: str) -> str:
    parts = urlsplit(url)
    safe_pairs = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if any(part in key.casefold() for part in SENSITIVE_QUERY_PARTS):
            safe_pairs.append((key, "<redacted>"))
        else:
            safe_pairs.append((key, value))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(safe_pairs), parts.fragment))


def header_names(headers: dict[str, str]) -> list[str]:
    return sorted(headers.keys(), key=str.casefold)


def sanitize_headers(headers: dict[str, str]) -> dict[str, str]:
    sanitized: dict[str, str] = {}
    for key, value in headers.items():
        sanitized[key] = "<redacted>" if key.casefold() in SENSITIVE_HEADERS else value
    return sanitized


def _shape(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return "..."
    if isinstance(value, dict):
        return {str(key): _shape(child, depth + 1) for key, child in list(value.items())[:80]}
    if isinstance(value, list):
        if not value:
            return []
        return [_shape(value[0], depth + 1), f"... {len(value)} item(s)"]
    if value is None:
        return None
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    return "str"


def body_shape(text: str | None, content_type: str | None = None) -> Any:
    if not text:
        return None
    trimmed = text[:MAX_BODY_CHARS]
    looks_json = (content_type and "json" in content_type.casefold()) or trimmed.lstrip().startswith(("{", "["))
    if looks_json:
        try:
            return _shape(json.loads(trimmed))
        except json.JSONDecodeError:
            return {"unparsed": f"{len(text)} chars"}
    if "application/x-www-form-urlencoded" in (content_type or "").casefold():
        return {
            key: "<redacted>" if any(part in key.casefold() for part in SENSITIVE_QUERY_PARTS) else "str"
            for key, _ in parse_qsl(trimmed)
        }
    return {"text": f"{len(text)} chars"}


@dataclass
class NetworkEntry:
    method: str
    url: str
    resource_type: str | None = None
    request_content_type: str | None = None
    request_header_names: list[str] = field(default_factory=list)
    request_body_shape: Any = None
    response_status: int | None = None
    response_content_type: str | None = None
    response_header_names: list[str] = field(default_factory=list)
    response_date: str | None = None
    response_body_shape: Any = None
    error: str | None = None


class NetworkRecorder:
    def __init__(self) -> None:
        self._entries_by_request: dict[Any, NetworkEntry] = {}
        self.entries: list[NetworkEntry] = []

    def attach(self, page: Any) -> None:
        page.on("request", self.on_request)
        page.on("response", self.on_response)
        page.on("requestfailed", self.on_request_failed)

    def on_request(self, request: Any) -> None:
        headers = request.headers
        content_type = headers.get("content-type")
        try:
            post_data = request.post_data
        except Exception:
            post_data = None
        entry = NetworkEntry(
            method=request.method,
            url=sanitize_url(request.url),
            resource_type=request.resource_type,
            request_content_type=content_type,
            request_header_names=header_names(headers),
            request_body_shape=body_shape(post_data, content_type),
        )
        self._entries_by_request[request] = entry
        self.entries.append(entry)

    def on_response(self, response: Any) -> None:
        entry = self._entries_by_request.get(response.request)
        if not entry:
            return
        headers = response.headers
        content_type = headers.get("content-type")
        entry.response_status = response.status
        entry.response_content_type = content_type
        entry.response_header_names = header_names(headers)
        entry.response_date = headers.get("date")
        if _is_interesting_response(response.url, content_type):
            try:
                entry.response_body_shape = body_shape(response.text(), content_type)
            except Exception as exc:
                entry.response_body_shape = {"unreadable": type(exc).__name__}

    def on_request_failed(self, request: Any) -> None:
        entry = self._entries_by_request.get(request)
        if entry:
            failure = request.failure
            entry.error = str(failure) if failure else "request failed"

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        serializable = [entry.__dict__ for entry in self.entries if _keep_entry(entry)]
        path.write_text(json.dumps(serializable, indent=2, ensure_ascii=False), encoding="utf-8")
        path.chmod(0o600)


def _is_interesting_response(url: str, content_type: str | None) -> bool:
    lower_url = url.casefold()
    lower_type = (content_type or "").casefold()
    return (
        "json" in lower_type
        or "/api/" in lower_url
        or "graphql" in lower_url
        or "booking" in lower_url
        or "activity" in lower_url
        or "reservation" in lower_url
    )


def _keep_entry(entry: NetworkEntry) -> bool:
    if entry.resource_type in {"image", "font", "media"}:
        return False
    return bool(
        entry.response_body_shape
        or entry.request_body_shape
        or re.search(r"/api/|graphql|booking|activity|reservation|schedule|login", entry.url, re.I)
    )


def timestamped_path(directory: Path, suffix: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return directory / f"{stamp}-{suffix}"
