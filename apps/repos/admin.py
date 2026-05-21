from django.contrib import admin, messages
from django_q.tasks import async_task

from apps.repos.models import AwesomeList, AwesomeListItem, Repository


@admin.action(description="Queue scan for selected awesome lists")
def queue_scan(modeladmin, request, queryset):
    for awesome_list in queryset:
        async_task(
            "apps.repos.tasks.sync_awesome_list_task",
            awesome_list.id,
            group="Scan awesome list",
        )
    messages.success(request, f"Queued {queryset.count()} awesome-list scan(s).")


@admin.register(AwesomeList)
class AwesomeListAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "repo_full_name", "is_active", "last_scanned_at", "item_count")
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ("name", "slug", "repo_full_name", "source_url")
    actions = [queue_scan]

    def item_count(self, obj):
        return obj.items.count()


@admin.register(Repository)
class RepositoryAdmin(admin.ModelAdmin):
    list_display = (
        "full_name",
        "stars",
        "language",
        "is_archived",
        "github_pushed_at",
        "awesome_count",
    )
    search_fields = ("full_name", "description", "language")
    list_filter = ("is_archived", "is_fork", "language")
    readonly_fields = ("raw",)

    def awesome_count(self, obj):
        return obj.awesome_items.count()


@admin.register(AwesomeListItem)
class AwesomeListItemAdmin(admin.ModelAdmin):
    list_display = ("awesome_list", "repository", "created_at")
    search_fields = ("awesome_list__name", "repository__full_name")
