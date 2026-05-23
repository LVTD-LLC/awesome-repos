import json

import pytest

from apps.repos.models import AwesomeList, AwesomeListItem, Repository


@pytest.fixture(autouse=True)
def shutdown_mcp_adapter():
    yield

    from apps.mcp_server.views import _mcp_adapter

    _mcp_adapter.shutdown()


def _mcp_headers(api_key: str, *, bearer: bool = True) -> dict:
    headers = {
        "HTTP_ACCEPT": "application/json, text/event-stream",
        "HTTP_MCP_PROTOCOL_VERSION": "2025-11-25",
    }
    if bearer:
        headers["HTTP_AUTHORIZATION"] = f"Bearer {api_key}"
    else:
        headers["HTTP_X_API_KEY"] = api_key
    return headers


@pytest.mark.django_db(transaction=True)
def test_mcp_initialize_and_tools_list(client, profile):
    api_key = profile.rotate_api_key()

    initialize_response = client.post(
        "/mcp",
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "1.0.0"},
                },
            }
        ),
        content_type="application/json",
        **_mcp_headers(api_key),
    )

    assert initialize_response.status_code == 200
    initialize_payload = initialize_response.json()
    assert initialize_payload["result"]["protocolVersion"] == "2025-11-25"
    assert "tools" in initialize_payload["result"]["capabilities"]
    assert initialize_payload["result"]["serverInfo"]["name"] == "awesome-repos"

    tools_response = client.post(
        "/mcp",
        data=json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        content_type="application/json",
        **_mcp_headers(api_key),
    )

    assert tools_response.status_code == 200
    tools = tools_response.json()["result"]["tools"]
    tool_names = {tool["name"] for tool in tools}
    assert {
        "search_repositories",
        "get_repository",
        "search_awesome_lists",
        "get_awesome_list",
        "search_awesome_list_repositories",
    } <= tool_names
    search_tool = next(tool for tool in tools if tool["name"] == "search_repositories")
    assert search_tool["annotations"]["readOnlyHint"] is True


@pytest.mark.django_db(transaction=True)
def test_mcp_search_repositories_tool_uses_shared_search_service(client, profile):
    awesome_list = AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
        repo_full_name="wsvincent/awesome-django",
    )
    django_repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        description="Python web framework",
        language="Python",
        stars=90000,
        topics=["django", "web"],
    )
    Repository.objects.create(
        full_name="expressjs/express",
        owner="expressjs",
        name="express",
        url="https://github.com/expressjs/express",
        description="Node web framework",
        language="JavaScript",
        stars=65000,
        topics=["node", "web"],
    )
    AwesomeListItem.objects.create(awesome_list=awesome_list, repository=django_repo)

    response = client.post(
        "/mcp",
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "search_repositories",
                    "arguments": {
                        "q": "framework",
                        "language": "Python",
                        "topic": "django",
                    },
                },
            }
        ),
        content_type="application/json",
        **_mcp_headers(profile.rotate_api_key(), bearer=False),
    )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["pagination"]["count"] == 1
    assert result["structuredContent"]["results"][0]["full_name"] == "django/django"
    assert "django/django" in result["content"][0]["text"]


@pytest.mark.django_db(transaction=True)
def test_mcp_auth_origin_get_and_notification_handling(client, profile):
    message = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

    unauthenticated_response = client.post(
        "/mcp",
        data=message,
        content_type="application/json",
        HTTP_ACCEPT="application/json, text/event-stream",
    )

    assert unauthenticated_response.status_code == 401

    invalid_origin_response = client.post(
        "/mcp",
        data=message,
        content_type="application/json",
        HTTP_ORIGIN="https://evil.example",
        **_mcp_headers(profile.rotate_api_key()),
    )

    assert invalid_origin_response.status_code == 403

    get_response = client.get(
        "/mcp",
        HTTP_ACCEPT="text/event-stream",
    )

    assert get_response.status_code == 405

    notification_response = client.post(
        "/mcp",
        data=json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        content_type="application/json",
        **_mcp_headers(profile.rotate_api_key()),
    )

    assert notification_response.status_code == 202
    assert notification_response.content == b""
