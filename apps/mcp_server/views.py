import asyncio
import atexit
import threading
from collections.abc import Coroutine, Iterable
from typing import Any
from urllib.parse import urlparse

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from starlette.types import ASGIApp

from apps.mcp_server.server import SUPPORTED_MCP_PROTOCOL_VERSIONS, mcp_asgi_app


class DjangoASGIAdapter:
    """Run the FastMCP ASGI app behind the existing Django URLconf."""

    def __init__(self, app: ASGIApp):
        self.app = app
        self._shutdown_event: asyncio.Event | None = None
        self._lifespan_task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._started = False
        self._lock = threading.Lock()
        atexit.register(self.shutdown)

    def __call__(self, request: HttpRequest) -> HttpResponse:
        self._ensure_started()
        status, headers, body = self._run_coroutine(
            self._call_asgi(self._scope_from_request(request), request.body),
        )

        response = HttpResponse(body, status=status)
        for name, value in headers:
            header_name = name.decode("latin-1")
            if header_name.lower() == "content-length":
                continue
            response[header_name] = value.decode("latin-1")
        return response

    def shutdown(self) -> None:
        if not self._started:
            return

        loop: asyncio.AbstractEventLoop | None
        thread: threading.Thread | None
        with self._lock:
            if not self._started:
                return
            loop = self._loop
            thread = self._thread
            shutdown_event = self._shutdown_event
            lifespan_task = self._lifespan_task
            self._shutdown_event = None
            self._lifespan_task = None
            self._started = False
            self._loop = None
            self._thread = None

        if loop is None:
            return

        try:
            asyncio.run_coroutine_threadsafe(
                self._shutdown_lifespan(shutdown_event, lifespan_task), loop
            ).result(timeout=10)
        finally:
            loop.call_soon_threadsafe(loop.stop)
            if thread is not None:
                thread.join(timeout=10)

    def _ensure_started(self) -> None:
        if self._started:
            return
        with self._lock:
            if self._started:
                return
            self._start_loop_locked()
            try:
                self._run_coroutine(self._startup())
            except Exception:
                self._stop_loop_locked()
                raise
            else:
                self._started = True

    async def _startup(self) -> None:
        ready = asyncio.Event()
        shutdown_event = asyncio.Event()

        async def run_lifespan() -> None:
            async with self.app.router.lifespan_context(self.app):
                ready.set()
                await shutdown_event.wait()

        lifespan_task = asyncio.create_task(run_lifespan())
        ready_task = asyncio.create_task(ready.wait())
        done, pending = await asyncio.wait(
            {ready_task, lifespan_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if ready_task in pending:
            ready_task.cancel()
        if lifespan_task in done:
            lifespan_task.result()

        self._shutdown_event = shutdown_event
        self._lifespan_task = lifespan_task

    async def _shutdown_lifespan(
        self,
        shutdown_event: asyncio.Event | None,
        lifespan_task: asyncio.Task | None,
    ) -> None:
        if shutdown_event is None or lifespan_task is None:
            return
        shutdown_event.set()
        await lifespan_task

    def _start_loop_locked(self) -> None:
        loop = asyncio.new_event_loop()
        ready = threading.Event()

        def run_loop() -> None:
            asyncio.set_event_loop(loop)
            ready.set()
            try:
                loop.run_forever()
            finally:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.close()

        thread = threading.Thread(
            target=run_loop,
            name="awesome-repos-mcp-asgi",
            daemon=True,
        )
        thread.start()
        ready.wait()
        self._loop = loop
        self._thread = thread

    def _stop_loop_locked(self) -> None:
        loop = self._loop
        thread = self._thread
        self._loop = None
        self._thread = None
        self._shutdown_event = None
        self._lifespan_task = None
        if loop is None:
            return
        loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=10)

    def _run_coroutine(self, coroutine: Coroutine[Any, Any, Any]) -> Any:
        loop = self._loop
        if loop is None:
            raise RuntimeError("MCP ASGI loop is not running.")
        return asyncio.run_coroutine_threadsafe(coroutine, loop).result()

    async def _call_asgi(
        self, scope: dict, body: bytes
    ) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
        request_sent = False
        status = 500
        headers: list[tuple[bytes, bytes]] = []
        body_parts: list[bytes] = []

        async def receive() -> dict:
            nonlocal request_sent
            if request_sent:
                return {"type": "http.request", "body": b"", "more_body": False}
            request_sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message: dict) -> None:
            nonlocal status, headers
            message_type = message["type"]
            if message_type == "http.response.start":
                status = message["status"]
                headers = list(message.get("headers", []))
            elif message_type == "http.response.body":
                body_parts.append(message.get("body", b""))

        await self.app(scope, receive, send)
        return status, headers, b"".join(body_parts)

    def _scope_from_request(self, request: HttpRequest) -> dict:
        return {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": _http_version(request),
            "method": request.method,
            "scheme": request.scheme,
            "path": request.path_info,
            "raw_path": request.path_info.encode("ascii", errors="ignore"),
            "query_string": request.META.get("QUERY_STRING", "").encode("ascii"),
            "root_path": "",
            "headers": list(_asgi_headers(request)),
            "client": _client_address(request),
            "server": _server_address(request),
        }


def _http_version(request: HttpRequest) -> str:
    protocol = request.META.get("SERVER_PROTOCOL", "HTTP/1.1")
    return protocol.split("/", 1)[-1]


def _client_address(request: HttpRequest) -> tuple[str, int]:
    port = request.META.get("REMOTE_PORT")
    return request.META.get("REMOTE_ADDR", ""), int(port) if str(port).isdigit() else 0


def _server_address(request: HttpRequest) -> tuple[str, int]:
    port = request.META.get("SERVER_PORT")
    return request.get_host().split(":", 1)[0], int(port) if str(port).isdigit() else 80


def _asgi_headers(request: HttpRequest) -> Iterable[tuple[bytes, bytes]]:
    headers = [
        (name.lower().encode("latin-1"), value.encode("latin-1"))
        for name, value in request.headers.items()
    ]
    has_authorization = any(name == b"authorization" for name, _value in headers)
    api_key = request.headers.get("X-API-Key", "").strip()
    if api_key and not has_authorization:
        headers.append((b"authorization", f"Bearer {api_key}".encode("latin-1")))
    return headers


def _origin_from_url(value: str) -> tuple[str, str, str]:
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        return "", "", ""
    host = parsed.hostname or ""
    return parsed.scheme.lower(), host.lower(), f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def _trusted_origin_patterns() -> list[str]:
    return [settings.SITE_URL, *getattr(settings, "CSRF_TRUSTED_ORIGINS", [])]


def _origin_allowed(request: HttpRequest) -> bool:
    origin = request.headers.get("Origin", "")
    if not origin:
        return True

    origin_scheme, origin_host, normalized_origin = _origin_from_url(origin)
    if not normalized_origin:
        return False

    for trusted in _trusted_origin_patterns():
        trusted_scheme, trusted_host, normalized_trusted = _origin_from_url(trusted)
        if normalized_trusted == normalized_origin:
            return True
        if trusted_host.startswith("*.") and origin_scheme == trusted_scheme:
            suffix = trusted_host.removeprefix("*")
            if origin_host.endswith(suffix):
                return True
    return False


def _validate_protocol_version(request: HttpRequest) -> JsonResponse | None:
    protocol_version = request.headers.get("MCP-Protocol-Version", "")
    if protocol_version and protocol_version not in SUPPORTED_MCP_PROTOCOL_VERSIONS:
        return JsonResponse(
            {
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32600,
                    "message": "Unsupported MCP protocol version.",
                    "data": {"supported": sorted(SUPPORTED_MCP_PROTOCOL_VERSIONS)},
                },
            },
            status=400,
        )
    return None


_mcp_adapter = DjangoASGIAdapter(mcp_asgi_app)


@csrf_exempt
@require_http_methods(["GET", "POST", "DELETE"])
def mcp_endpoint(request: HttpRequest) -> HttpResponse:
    if not _origin_allowed(request):
        return JsonResponse(
            {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32600, "message": "Invalid Origin header."},
            },
            status=403,
        )

    version_error = _validate_protocol_version(request)
    if version_error is not None:
        return version_error

    return _mcp_adapter(request)
