from django import template

from apps.repos.stack_detection import package_manager_label as format_package_manager_label

register = template.Library()


@register.filter
def package_manager_label(slug: str) -> str:
    return format_package_manager_label(slug)
