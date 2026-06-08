from django import template

from apps.repos.stack_detection import package_manager_label as format_package_manager_label

register = template.Library()


@register.filter
def package_manager_label(slug: str) -> str:
    return format_package_manager_label(slug)


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
