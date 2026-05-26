from django.contrib import admin, messages
from django.utils import timezone
from django_q.tasks import async_task

from apps.repos.models import (
    AwesomeList,
    AwesomeListItem,
    AwesomeListRequest,
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
        "first_commit_at",
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
        "first_commit_at",
        "raw",
    )
    actions = [queue_scan]

    def item_count(self, obj):
        return obj.items.count()


@admin.register(AwesomeListRequest)
class AwesomeListRequestAdmin(admin.ModelAdmin):
    list_display = ("repo_full_name", "status", "requester_email", "created_at", "reviewed_at")
    search_fields = ("repo_full_name", "source_url", "requester_email", "note")
    list_filter = ("status", "created_at", "reviewed_at")
    readonly_fields = ("source_url", "repo_full_name", "created_at", "updated_at")

    def save_model(self, request, obj, form, change):
        if obj.status == AwesomeListRequest.Status.PENDING:
            obj.reviewed_at = None
        elif obj.reviewed_at is None:
            obj.reviewed_at = timezone.now()
        super().save_model(request, obj, form, change)


@admin.register(Repository)
class RepositoryAdmin(admin.ModelAdmin):
    list_display = (
        "full_name",
        "stars",
        "commit_count",
        "first_commit_at",
        "language",
        "generated_tags",
        "is_archived",
        "uses_ai_for_development",
        "github_pushed_at",
        "awesome_count",
    )
    search_fields = ("full_name", "description", "language", "topics", "generated_tags")
    list_filter = ("uses_ai_for_development", "is_archived", "is_fork", "language")
    readonly_fields = (
        "readme",
        "readme_path",
        "readme_url",
        "readme_synced_at",
        "readme_last_error",
        "ai_development_signals",
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
        "commit_count",
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
        "commit_count",
        "open_issues",
        "watchers",
        "default_branch",
        "is_archived",
        "is_disabled",
        "is_fork",
        "github_created_at",
        "github_updated_at",
        "github_pushed_at",
        "first_commit_at",
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
