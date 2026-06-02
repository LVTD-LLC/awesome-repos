import json
from collections.abc import Callable
from typing import Annotated, Any

from django.core.serializers.json import DjangoJSONEncoder
from django.db import close_old_connections
from django.http import Http404
from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from apps.repos.search_services import (
    get_awesome_list_detail_payload,
    get_repository_detail_payload,
    search_awesome_list_repositories_payload,
    search_awesome_lists_payload,
    search_repositories_payload,
)

READ_ONLY_TOOL = ToolAnnotations(readOnlyHint=True, idempotentHint=True)


def _safe_payload(payload: dict) -> dict:
    return json.loads(json.dumps(payload, cls=DjangoJSONEncoder))


def _positive_int(value: int, name: str) -> int:
    if value < 1:
        raise ValueError(f"{name} must be positive.")
    return value


def _not_found_error(exc: Http404) -> ValueError:
    return ValueError("No matching record was found.")


def _run_read_only_tool(callback: Callable[[], dict]) -> dict[str, Any]:
    close_old_connections()
    try:
        return _safe_payload(callback())
    finally:
        close_old_connections()


def register_tools(server: FastMCP) -> None:  # noqa: C901
    @server.tool(
        name="search_repositories",
        title="Search Repositories",
        annotations=READ_ONLY_TOOL,
    )
    def search_repositories(
        q: Annotated[
            str,
            Field(description="Search query for names, descriptions, topics, tags, and stacks."),
        ] = "",
        mode: Annotated[
            str,
            Field(description="Use semantic relevance when set to 'semantic'."),
        ] = "",
        list: Annotated[str, Field(description="Awesome-list slug to restrict results.")] = "",
        language: Annotated[str, Field(description="Exact repository language filter.")] = "",
        topic: Annotated[str, Field(description="GitHub topic filter.")] = "",
        generated_tag: Annotated[
            str,
            Field(description="AI-generated repository discovery tag filter."),
        ] = "",
        framework: Annotated[
            str,
            Field(description="Detected framework slug filter; aliases the stack filter."),
        ] = "",
        stack: Annotated[str, Field(description="Detected framework or stack slug filter.")] = "",
        package_manager: Annotated[
            str,
            Field(description="Detected package manager slug filter."),
        ] = "",
        min_stars: Annotated[int | None, Field(ge=0)] = None,
        updated_days: Annotated[int | None, Field(ge=1)] = None,
        unmaintained_days: Annotated[int | None, Field(ge=1)] = None,
        min_age_years: Annotated[int | None, Field(ge=1)] = None,
        min_velocity_percent: Annotated[int | None, Field(ge=0)] = None,
        min_liability_percent: Annotated[int | None, Field(ge=0)] = None,
        archived: Annotated[str, Field(description="'yes', 'no', or blank.")] = "",
        ai_development: Annotated[str, Field(description="'yes', 'no', or blank.")] = "",
        sort: Annotated[
            str,
            Field(
                description=(
                    "Sort by stars, recent, created, oldest, commits, velocity, "
                    "liability, awesome, or name."
                ),
            ),
        ] = "stars",
        sort_direction: Annotated[
            str,
            Field(description="'asc', 'desc', or blank for the default direction per sort."),
        ] = "",
        page: Annotated[int, Field(ge=1)] = 1,
        page_size: Annotated[int, Field(ge=1, le=100)] = 30,
    ) -> dict:
        """Search GitHub repositories indexed from awesome lists."""
        return _run_read_only_tool(
            lambda: search_repositories_payload(
                q=q,
                mode=mode,
                list_slug=list,
                language=language,
                topic=topic,
                generated_tag=generated_tag,
                framework=framework,
                stack=stack,
                package_manager=package_manager,
                min_stars=min_stars,
                updated_days=updated_days,
                unmaintained_days=unmaintained_days,
                min_age_years=min_age_years,
                min_velocity_percent=min_velocity_percent,
                min_liability_percent=min_liability_percent,
                archived=archived,
                ai_development=ai_development,
                sort=sort,
                sort_direction=sort_direction,
                page=page,
                page_size=page_size,
            )
        )

    @server.tool(
        name="get_repository",
        title="Get Repository",
        annotations=READ_ONLY_TOOL,
    )
    def get_repository(
        full_name: Annotated[
            str,
            Field(description="Repository full name, for example django/django."),
        ],
        include_readme: Annotated[
            bool,
            Field(description="Include README text in the result."),
        ] = False,
        max_readme_chars: Annotated[
            int,
            Field(ge=1, le=24000, description="Maximum README characters to return."),
        ] = 4000,
    ) -> dict:
        """Fetch one indexed GitHub repository by owner/name."""
        if "/" not in full_name:
            raise ValueError("full_name must use the owner/name format.")

        def payload() -> dict:
            owner, name = full_name.split("/", 1)
            try:
                result = get_repository_detail_payload(owner=owner, name=name)
            except Http404 as exc:
                raise _not_found_error(exc) from exc

            if include_readme:
                readme_limit = _positive_int(max_readme_chars, "max_readme_chars")
                readme = result.get("readme", "")
                if len(readme) > readme_limit:
                    result["readme"] = readme[:readme_limit]
                    result["readme_truncated"] = True
                    result["readme_total_chars"] = len(readme)
            else:
                result.pop("readme", None)
                result["readme_omitted"] = True
            return result

        return _run_read_only_tool(payload)

    @server.tool(
        name="search_awesome_lists",
        title="Search Awesome Lists",
        annotations=READ_ONLY_TOOL,
    )
    def search_awesome_lists(
        q: Annotated[
            str,
            Field(description="Search query for list name, repo name, topic, or description."),
        ] = "",
        min_age_years: Annotated[int | None, Field(ge=1)] = None,
        sort: Annotated[
            str,
            Field(
                description=(
                    "Sort by stars, repos, indexed, commits, recent, oldest, scanned, or name."
                ),
            ),
        ] = "stars",
        page: Annotated[int, Field(ge=1)] = 1,
        page_size: Annotated[int, Field(ge=1, le=100)] = 30,
    ) -> dict:
        """Search active awesome-lists tracked by Awesome."""
        return _run_read_only_tool(
            lambda: search_awesome_lists_payload(
                q=q,
                min_age_years=min_age_years,
                sort=sort,
                page=page,
                page_size=page_size,
            )
        )

    @server.tool(
        name="get_awesome_list",
        title="Get Awesome List",
        annotations=READ_ONLY_TOOL,
    )
    def get_awesome_list(
        slug: Annotated[str, Field(description="Awesome-list slug.")],
    ) -> dict:
        """Fetch one active awesome list by slug."""
        if not slug:
            raise ValueError("slug is required.")

        def payload() -> dict:
            try:
                return get_awesome_list_detail_payload(slug=slug)
            except Http404 as exc:
                raise _not_found_error(exc) from exc

        return _run_read_only_tool(payload)

    @server.tool(
        name="search_awesome_list_repositories",
        title="Search Awesome List Repositories",
        annotations=READ_ONLY_TOOL,
    )
    def search_awesome_list_repositories(
        slug: Annotated[str, Field(description="Awesome-list slug.")],
        q: Annotated[
            str,
            Field(description="Search query for names, descriptions, topics, tags, and stacks."),
        ] = "",
        language: Annotated[str, Field(description="Exact repository language filter.")] = "",
        topic: Annotated[str, Field(description="GitHub topic filter.")] = "",
        generated_tag: Annotated[
            str,
            Field(description="AI-generated repository discovery tag filter."),
        ] = "",
        framework: Annotated[
            str,
            Field(description="Detected framework slug filter; aliases the stack filter."),
        ] = "",
        stack: Annotated[str, Field(description="Detected framework or stack slug filter.")] = "",
        package_manager: Annotated[
            str,
            Field(description="Detected package manager slug filter."),
        ] = "",
        min_stars: Annotated[int | None, Field(ge=0)] = None,
        updated_days: Annotated[int | None, Field(ge=1)] = None,
        unmaintained_days: Annotated[int | None, Field(ge=1)] = None,
        min_age_years: Annotated[int | None, Field(ge=1)] = None,
        min_velocity_percent: Annotated[int | None, Field(ge=0)] = None,
        min_liability_percent: Annotated[int | None, Field(ge=0)] = None,
        archived: Annotated[str, Field(description="'yes', 'no', or blank.")] = "",
        ai_development: Annotated[str, Field(description="'yes', 'no', or blank.")] = "",
        sort: Annotated[
            str,
            Field(
                description=(
                    "Sort by stars, recent, created, oldest, commits, velocity, "
                    "liability, awesome, or name."
                ),
            ),
        ] = "stars",
        sort_direction: Annotated[
            str,
            Field(description="'asc', 'desc', or blank for the default direction per sort."),
        ] = "",
        page: Annotated[int, Field(ge=1)] = 1,
        page_size: Annotated[int, Field(ge=1, le=100)] = 50,
    ) -> dict:
        """Search repositories indexed from one awesome list."""
        if not slug:
            raise ValueError("slug is required.")

        def payload() -> dict:
            try:
                return search_awesome_list_repositories_payload(
                    slug=slug,
                    q=q,
                    language=language,
                    topic=topic,
                    generated_tag=generated_tag,
                    framework=framework,
                    stack=stack,
                    package_manager=package_manager,
                    min_stars=min_stars,
                    updated_days=updated_days,
                    unmaintained_days=unmaintained_days,
                    min_age_years=min_age_years,
                    min_velocity_percent=min_velocity_percent,
                    min_liability_percent=min_liability_percent,
                    archived=archived,
                    ai_development=ai_development,
                    sort=sort,
                    sort_direction=sort_direction,
                    page=page,
                    page_size=page_size,
                )
            except Http404 as exc:
                raise _not_found_error(exc) from exc

        return _run_read_only_tool(payload)
