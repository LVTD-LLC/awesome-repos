import json
from collections.abc import Callable
from urllib.parse import urlparse

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.api.auth import get_profile_for_api_key
from apps.api.services import (
    get_awesome_list_detail_payload,
    get_repository_detail_payload,
    search_awesome_list_repositories_payload,
    search_awesome_lists_payload,
    search_repositories_payload,
)
from awesome_repos.utils import get_awesome_repos_logger

logger = get_awesome_repos_logger(__name__)

MCP_PROTOCOL_VERSION = "2025-11-25"
SUPPORTED_MCP_PROTOCOL_VERSIONS = {
    MCP_PROTOCOL_VERSION,
    "2025-06-18",
    "2025-03-26",
}

JSONRPC_VERSION = "2.0"
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
UNAUTHORIZED = -32001


def _object_schema(properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


REPOSITORY_FILTER_PROPERTIES = {
    "q": {"type": "string", "description": "Search query for names, descriptions, topics, tags."},
    "mode": {
        "type": "string",
        "enum": ["", "semantic"],
        "description": "Use semantic relevance when repository embeddings are configured.",
    },
    "list": {"type": "string", "description": "Awesome-list slug to restrict results."},
    "language": {"type": "string", "description": "Exact repository language filter."},
    "topic": {"type": "string", "description": "GitHub topic filter."},
    "generated_tag": {"type": "string", "description": "AI-generated discovery tag filter."},
    "min_stars": {"type": "integer", "minimum": 0},
    "updated_days": {"type": "integer", "minimum": 1},
    "archived": {"type": "string", "enum": ["", "yes", "no"]},
    "ai_development": {"type": "string", "enum": ["", "yes", "no"]},
    "sort": {
        "type": "string",
        "enum": ["stars", "recent", "created", "commits", "awesome", "name"],
    },
    "page": {"type": "integer", "minimum": 1},
    "page_size": {"type": "integer", "minimum": 1, "maximum": 100},
}

AWESOME_LIST_REPOSITORY_FILTER_PROPERTIES = {
    key: value for key, value in REPOSITORY_FILTER_PROPERTIES.items() if key not in {"mode", "list"}
}

MCP_TOOLS = [
    {
        "name": "search_repositories",
        "title": "Search Repositories",
        "description": "Search GitHub repositories indexed from awesome lists.",
        "inputSchema": _object_schema(REPOSITORY_FILTER_PROPERTIES),
        "annotations": {"readOnlyHint": True, "idempotentHint": True},
    },
    {
        "name": "get_repository",
        "title": "Get Repository",
        "description": "Fetch one indexed GitHub repository by full name.",
        "inputSchema": _object_schema(
            {
                "full_name": {
                    "type": "string",
                    "description": "Repository full name, for example django/django.",
                },
                "include_readme": {
                    "type": "boolean",
                    "description": "Include README text in the result. Defaults to false.",
                },
                "max_readme_chars": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 24000,
                    "description": (
                        "Maximum README characters to return when include_readme is true."
                    ),
                },
            },
            required=["full_name"],
        ),
        "annotations": {"readOnlyHint": True, "idempotentHint": True},
    },
    {
        "name": "search_awesome_lists",
        "title": "Search Awesome Lists",
        "description": "Search active awesome lists tracked by Awesome Repos.",
        "inputSchema": _object_schema(
            {
                "q": {
                    "type": "string",
                    "description": "Search query for list name, repo name, topic, or description.",
                },
                "sort": {
                    "type": "string",
                    "enum": ["stars", "repos", "indexed", "commits", "recent", "scanned", "name"],
                },
                "page": {"type": "integer", "minimum": 1},
                "page_size": {"type": "integer", "minimum": 1, "maximum": 100},
            }
        ),
        "annotations": {"readOnlyHint": True, "idempotentHint": True},
    },
    {
        "name": "get_awesome_list",
        "title": "Get Awesome List",
        "description": "Fetch one active awesome list by slug.",
        "inputSchema": _object_schema(
            {"slug": {"type": "string", "description": "Awesome-list slug."}},
            required=["slug"],
        ),
        "annotations": {"readOnlyHint": True, "idempotentHint": True},
    },
    {
        "name": "search_awesome_list_repositories",
        "title": "Search Awesome List Repositories",
        "description": "Search repositories indexed from one awesome list.",
        "inputSchema": _object_schema(
            {
                "slug": {"type": "string", "description": "Awesome-list slug."},
                **AWESOME_LIST_REPOSITORY_FILTER_PROPERTIES,
            },
            required=["slug"],
        ),
        "annotations": {"readOnlyHint": True, "idempotentHint": True},
    },
]


def _int_argument(arguments: dict, name: str, default: int | None = None) -> int | None:
    value = arguments.get(name)
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer.") from exc
    if parsed < 0:
        raise ValueError(f"{name} must be non-negative.")
    return parsed


def _string_argument(arguments: dict, name: str, default: str = "") -> str:
    value = arguments.get(name, default)
    if value is None:
        return default
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string.")
    return value.strip()


def _bool_argument(arguments: dict, name: str, default: bool = False) -> bool:
    value = arguments.get(name, default)
    if isinstance(value, bool):
        return value
    if value in ("true", "True", "1", 1):
        return True
    if value in ("false", "False", "0", 0, None, ""):
        return False
    raise ValueError(f"{name} must be a boolean.")


def _search_repositories(arguments: dict) -> dict:
    return search_repositories_payload(
        q=_string_argument(arguments, "q"),
        mode=_string_argument(arguments, "mode"),
        list_slug=_string_argument(arguments, "list"),
        language=_string_argument(arguments, "language"),
        topic=_string_argument(arguments, "topic"),
        generated_tag=_string_argument(arguments, "generated_tag"),
        min_stars=_int_argument(arguments, "min_stars"),
        updated_days=_int_argument(arguments, "updated_days"),
        archived=_string_argument(arguments, "archived"),
        ai_development=_string_argument(arguments, "ai_development"),
        sort=_string_argument(arguments, "sort", "stars"),
        page=_int_argument(arguments, "page", 1) or 1,
        page_size=_int_argument(arguments, "page_size", 30) or 30,
    )


def _get_repository(arguments: dict) -> dict:
    full_name = _string_argument(arguments, "full_name")
    if "/" not in full_name:
        raise ValueError("full_name must use the owner/name format.")
    owner, name = full_name.split("/", 1)
    payload = get_repository_detail_payload(owner=owner, name=name)

    include_readme = _bool_argument(arguments, "include_readme", False)
    if include_readme:
        max_readme_chars = _int_argument(arguments, "max_readme_chars", 4000) or 4000
        readme = payload.get("readme", "")
        if len(readme) > max_readme_chars:
            payload["readme"] = readme[:max_readme_chars]
            payload["readme_truncated"] = True
            payload["readme_total_chars"] = len(readme)
    else:
        payload.pop("readme", None)
        payload["readme_omitted"] = True
    return payload


def _search_awesome_lists(arguments: dict) -> dict:
    return search_awesome_lists_payload(
        q=_string_argument(arguments, "q"),
        sort=_string_argument(arguments, "sort", "stars"),
        page=_int_argument(arguments, "page", 1) or 1,
        page_size=_int_argument(arguments, "page_size", 30) or 30,
    )


def _get_awesome_list(arguments: dict) -> dict:
    slug = _string_argument(arguments, "slug")
    if not slug:
        raise ValueError("slug is required.")
    return get_awesome_list_detail_payload(slug=slug)


def _search_awesome_list_repositories(arguments: dict) -> dict:
    slug = _string_argument(arguments, "slug")
    if not slug:
        raise ValueError("slug is required.")
    return search_awesome_list_repositories_payload(
        slug=slug,
        q=_string_argument(arguments, "q"),
        language=_string_argument(arguments, "language"),
        topic=_string_argument(arguments, "topic"),
        generated_tag=_string_argument(arguments, "generated_tag"),
        min_stars=_int_argument(arguments, "min_stars"),
        updated_days=_int_argument(arguments, "updated_days"),
        archived=_string_argument(arguments, "archived"),
        ai_development=_string_argument(arguments, "ai_development"),
        sort=_string_argument(arguments, "sort", "stars"),
        page=_int_argument(arguments, "page", 1) or 1,
        page_size=_int_argument(arguments, "page_size", 50) or 50,
    )


MCP_TOOL_HANDLERS: dict[str, Callable[[dict], dict]] = {
    "search_repositories": _search_repositories,
    "get_repository": _get_repository,
    "search_awesome_lists": _search_awesome_lists,
    "get_awesome_list": _get_awesome_list,
    "search_awesome_list_repositories": _search_awesome_list_repositories,
}


def _jsonrpc_result(message_id, result: dict) -> dict:
    return {"jsonrpc": JSONRPC_VERSION, "id": message_id, "result": result}


def _jsonrpc_error(message_id, code: int, message: str, data=None) -> dict:
    payload = {
        "jsonrpc": JSONRPC_VERSION,
        "id": message_id,
        "error": {"code": code, "message": message},
    }
    if data is not None:
        payload["error"]["data"] = data
    return payload


def _json_response(payload: dict, *, status: int = 200) -> JsonResponse:
    return JsonResponse(payload, encoder=DjangoJSONEncoder, status=status)


def _safe_structured_content(payload: dict) -> dict:
    return json.loads(json.dumps(payload, cls=DjangoJSONEncoder))


def _tool_result(payload: dict) -> dict:
    safe_payload = _safe_structured_content(payload)
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(safe_payload, indent=2),
            }
        ],
        "structuredContent": safe_payload,
        "isError": False,
    }


def _tool_error(message: str) -> dict:
    return {
        "content": [{"type": "text", "text": message}],
        "isError": True,
    }


def _api_key_from_request(request: HttpRequest) -> str:
    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() == "bearer" and token:
        return token.strip()
    return request.headers.get("X-API-Key", "").strip()


def _request_profile(request: HttpRequest):
    api_key = _api_key_from_request(request)
    if not api_key:
        return None
    return get_profile_for_api_key(api_key)


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
        return _json_response(
            _jsonrpc_error(
                None,
                INVALID_REQUEST,
                "Unsupported MCP protocol version.",
                {"supported": sorted(SUPPORTED_MCP_PROTOCOL_VERSIONS)},
            ),
            status=400,
        )
    return None


def _initialize_result(params: dict) -> dict:
    requested_version = params.get("protocolVersion")
    protocol_version = (
        requested_version
        if requested_version in SUPPORTED_MCP_PROTOCOL_VERSIONS
        else MCP_PROTOCOL_VERSION
    )
    return {
        "protocolVersion": protocol_version,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {
            "name": "awesome-repos",
            "title": "Awesome Repos",
            "version": "0.1.0",
            "description": "Search and monitor GitHub repositories listed across awesome lists.",
            "websiteUrl": settings.SITE_URL,
        },
        "instructions": (
            "Use these read-only tools to search Awesome Repos data. "
            "Authenticate with the same API key used for the HTTP API."
        ),
    }


def _handle_tools_call(params: dict) -> dict:
    tool_name = params.get("name")
    arguments = params.get("arguments") or {}
    if not isinstance(tool_name, str) or not tool_name:
        raise ValueError("Tool name is required.")
    if not isinstance(arguments, dict):
        raise ValueError("Tool arguments must be an object.")

    handler = MCP_TOOL_HANDLERS.get(tool_name)
    if handler is None:
        raise ValueError(f"Unknown tool: {tool_name}")

    try:
        return _tool_result(handler(arguments))
    except Http404:
        return _tool_error("No matching Awesome Repos record was found.")
    except ValueError as exc:
        return _tool_error(str(exc))


def _handle_mcp_request(message: dict) -> dict:
    message_id = message.get("id")
    method = message.get("method")
    params = message.get("params") or {}

    if message.get("jsonrpc") != JSONRPC_VERSION or not isinstance(method, str):
        return _jsonrpc_error(message_id, INVALID_REQUEST, "Invalid JSON-RPC request.")
    if not isinstance(params, dict):
        return _jsonrpc_error(message_id, INVALID_PARAMS, "params must be an object.")

    if method == "initialize":
        return _jsonrpc_result(message_id, _initialize_result(params))
    if method == "ping":
        return _jsonrpc_result(message_id, {})
    if method == "tools/list":
        return _jsonrpc_result(message_id, {"tools": MCP_TOOLS})
    if method == "tools/call":
        try:
            return _jsonrpc_result(message_id, _handle_tools_call(params))
        except ValueError as exc:
            return _jsonrpc_error(message_id, INVALID_PARAMS, str(exc))
        except Exception as exc:  # noqa: BLE001 - keep MCP failures in protocol format
            logger.error("mcp_tool_call_failed", error=str(exc), exc_info=True)
            return _jsonrpc_error(message_id, INTERNAL_ERROR, "Tool call failed.")

    return _jsonrpc_error(message_id, METHOD_NOT_FOUND, f"Unknown method: {method}")


@csrf_exempt
@require_http_methods(["GET", "POST"])
def mcp_endpoint(request: HttpRequest):
    if not _origin_allowed(request):
        return _json_response(
            _jsonrpc_error(None, INVALID_REQUEST, "Invalid Origin header."),
            status=403,
        )

    if request.method == "GET":
        return HttpResponse(status=405, headers={"Allow": "POST"})

    version_error = _validate_protocol_version(request)
    if version_error is not None:
        return version_error

    if _request_profile(request) is None:
        return _json_response(
            _jsonrpc_error(None, UNAUTHORIZED, "Missing or invalid API key."),
            status=401,
        )

    try:
        message = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _json_response(_jsonrpc_error(None, PARSE_ERROR, "Parse error."), status=400)

    if not isinstance(message, dict):
        return _json_response(
            _jsonrpc_error(None, INVALID_REQUEST, "Request body must be one JSON-RPC object."),
            status=400,
        )

    if "id" not in message:
        return HttpResponse(status=202)

    return _json_response(_handle_mcp_request(message))
