from django.contrib import admin, messages
from django_q.tasks import async_task

from apps.repos.models import (
    AwesomeList,
    AwesomeListItem,
    Repository,
    RepositoryEmbedding,
    RepositorySnapshot,
)


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
    list_display = (
        "name",
        "slug",
        "repo_full_name",
        "stars",
        "readme_repository_count",
        "commits_count",
        "is_active",
        "last_scanned_at",
        "item_count",
    )
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ("name", "slug", "repo_full_name", "source_url", "description")
    list_filter = ("is_active", "is_archived", "is_disabled")
    readonly_fields = (
        "topics",
        "stars",
        "forks",
        "open_issues",
        "watchers",
        "commits_count",
        "readme_repository_count",
        "default_branch",
        "is_archived",
        "is_disabled",
        "github_created_at",
        "github_updated_at",
        "github_pushed_at",
        "raw",
    )
    actions = [queue_scan]

    def item_count(self, obj):
        return obj.items.count()


@admin.register(Repository)
class RepositoryAdmin(admin.ModelAdmin):
    list_display = (
        "full_name",
        "stars",
        "language",
        "generated_tags",
        "is_archived",
        "github_pushed_at",
        "awesome_count",
    )
    search_fields = ("full_name", "description", "language", "topics", "generated_tags")
    list_filter = ("is_archived", "is_fork", "language")
    readonly_fields = (
        "readme",
        "readme_path",
        "readme_url",
        "readme_synced_at",
        "readme_last_error",
        "generated_tags",
        "generated_tags_model",
        "generated_tags_source_hash",
        "generated_tags_synced_at",
        "generated_tags_last_error",
        "raw",
    )

    def awesome_count(self, obj):
        return obj.awesome_items.count()


@admin.register(RepositorySnapshot)
class RepositorySnapshotAdmin(admin.ModelAdmin):
    list_display = (
        "repository",
        "captured_at",
        "stars",
        "forks",
        "watchers",
        "open_issues",
        "source",
    )
    search_fields = ("repository__full_name", "language")
    list_filter = ("source", "is_archived", "language")
    readonly_fields = (
        "repository",
        "captured_at",
        "source",
        "description",
        "homepage_url",
        "language",
        "license_name",
        "topics",
        "stars",
        "forks",
        "open_issues",
        "watchers",
        "default_branch",
        "is_archived",
        "is_disabled",
        "is_fork",
        "github_created_at",
        "github_updated_at",
        "github_pushed_at",
    )
    date_hierarchy = "captured_at"

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AwesomeListItem)
class AwesomeListItemAdmin(admin.ModelAdmin):
    list_display = ("awesome_list", "repository", "created_at")
    search_fields = ("awesome_list__name", "repository__full_name")


@admin.register(RepositoryEmbedding)
class RepositoryEmbeddingAdmin(admin.ModelAdmin):
    list_display = ("repository", "model", "dimensions", "source_text_chars", "embedded_at")
    search_fields = ("repository__full_name", "model", "source_text_hash")
    readonly_fields = ("embedding", "source_text_hash", "source_text_chars", "embedded_at")
