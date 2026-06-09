from django import template

from apps.repos.stack_detection import package_manager_label as format_package_manager_label

register = template.Library()


@register.filter
def package_manager_label(slug: str) -> str:
    return format_package_manager_label(slug)


@register.filter
def repository_issues_label(repository) -> str:
    raw = getattr(repository, "raw", None) or {}
    open_issues = getattr(repository, "open_issues", None)

    if isinstance(raw, dict):
        if raw.get("has_issues") is False:
            return "Disabled"
        has_raw_issue_count = raw.get("open_issues_count") is not None
    else:
        has_raw_issue_count = False

    if open_issues:
        return f"{int(open_issues):,} open"
    if has_raw_issue_count:
        return "0 open"
    return "Unknown"


@register.filter
def signed_intcomma(value) -> str:
    if value is None or value == "":
        return "n/a"
    try:
        number = int(value)
    except (TypeError, ValueError):
        return "n/a"
    sign = "+" if number > 0 else ""
    return f"{sign}{number:,}"


@register.filter
def signed_percent(value) -> str:
    if value is None or value == "":
        return "n/a"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if abs(number) < 0.05:
        number = 0
    sign = "+" if number > 0 else ""
    return f"{sign}{number:,.1f}%"
