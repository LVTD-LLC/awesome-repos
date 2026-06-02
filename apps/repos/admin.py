from django.contrib import admin, messages
from django.utils import timezone
from django_q.tasks import async_task

from apps.repos.models import (
    AwesomeList,
    AwesomeListItem,
    AwesomeListRequest,
    NewsletterIssueDelivery,
    NewsletterSubscription,
    Repository,
    RepositoryCommit,
    RepositoryEmbedding,
    RepositoryLike,
    RepositoryNewsletterIssue,
    RepositorySnapshot,
    UserStarredRepository,
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
        "detected_stacks",
        "package_managers",
        "generated_tags",
        "is_archived",
        "uses_ai_for_development",
        "is_awesome_list_candidate",
        "newsletter_tracking_enabled",
        "github_pushed_at",
        "awesome_count",
    )
    search_fields = (
        "full_name",
        "description",
        "language",
        "topics",
        "generated_tags",
        "detected_stacks",
        "package_managers",
    )
    list_filter = (
        "newsletter_tracking_enabled",
        "uses_ai_for_development",
        "is_awesome_list_candidate",
        "is_archived",
        "is_fork",
        "language",
    )
    readonly_fields = (
        "readme",
        "readme_path",
        "readme_url",
        "readme_synced_at",
        "readme_last_error",
        "dependency_files",
        "dependency_ecosystems",
        "package_managers",
        "detected_stacks",
        "stack_signals",
        "stack_detected_at",
        "stack_detection_last_error",
        "ai_development_signals",
        "awesome_list_detected_repo_count",
        "awesome_list_detection_reasons",
        "generated_tags",
        "generated_tags_model",
        "generated_tags_source_hash",
        "generated_tags_synced_at",
        "generated_tags_last_error",
        "newsletter_tracking_started_at",
        "newsletter_tracking_last_polled_at",
        "newsletter_tracking_last_error",
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


@admin.register(RepositoryLike)
class RepositoryLikeAdmin(admin.ModelAdmin):
    list_display = ("user", "repository", "created_at")
    search_fields = ("user__username", "user__email", "repository__full_name")
    list_filter = ("created_at",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(UserStarredRepository)
class UserStarredRepositoryAdmin(admin.ModelAdmin):
    list_display = (
        "profile",
        "repository",
        "starred_at",
        "last_seen_at",
        "last_synced_at",
    )
    search_fields = ("profile__user__email", "profile__user__username", "repository__full_name")
    list_filter = ("last_seen_at", "last_synced_at")
    readonly_fields = ("profile", "repository", "created_at", "updated_at")


@admin.register(RepositoryEmbedding)
class RepositoryEmbeddingAdmin(admin.ModelAdmin):
    list_display = ("repository", "model", "dimensions", "source_text_chars", "embedded_at")
    search_fields = ("repository__full_name", "model", "source_text_hash")
    readonly_fields = ("embedding", "source_text_hash", "source_text_chars", "embedded_at")


@admin.register(NewsletterSubscription)
class NewsletterSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("email", "repository", "cadence", "is_active", "unsubscribed_at", "user")
    search_fields = ("email", "repository__full_name", "user__email", "user__username")
    list_filter = ("cadence", "is_active", "created_at", "unsubscribed_at")
    readonly_fields = ("unsubscribe_token", "created_at", "updated_at")


@admin.register(RepositoryCommit)
class RepositoryCommitAdmin(admin.ModelAdmin):
    list_display = (
        "repository",
        "short_sha",
        "branch",
        "committed_at",
        "additions",
        "deletions",
        "changed_files",
        "summarized_at",
    )
    search_fields = ("repository__full_name", "sha", "message", "author_name", "author_login")
    list_filter = ("branch", "patch_truncated", "summarized_at", "committed_at")
    readonly_fields = (
        "repository",
        "sha",
        "branch",
        "message",
        "html_url",
        "api_url",
        "author_name",
        "author_email",
        "author_login",
        "authored_at",
        "committer_name",
        "committer_email",
        "committer_login",
        "committed_at",
        "parent_shas",
        "additions",
        "deletions",
        "changed_files",
        "files",
        "patch_truncated",
        "raw_metadata",
        "summary",
        "summary_model",
        "summary_source_hash",
        "summarized_at",
        "summary_last_error",
        "created_at",
        "updated_at",
    )
    date_hierarchy = "committed_at"

    def short_sha(self, obj):
        return obj.sha[:12]


@admin.register(RepositoryNewsletterIssue)
class RepositoryNewsletterIssueAdmin(admin.ModelAdmin):
    list_display = (
        "repository",
        "cadence",
        "period_start",
        "period_end",
        "commit_count",
        "published_at",
    )
    search_fields = ("repository__full_name", "title", "content_markdown")
    list_filter = ("cadence", "published_at", "period_start")
    readonly_fields = (
        "repository",
        "cadence",
        "period_start",
        "period_end",
        "slug",
        "title",
        "content_markdown",
        "content_html",
        "commit_count",
        "published_at",
        "generation_model",
        "generation_source_hash",
        "generated_at",
        "generation_last_error",
        "created_at",
        "updated_at",
    )
    date_hierarchy = "period_start"


@admin.register(NewsletterIssueDelivery)
class NewsletterIssueDeliveryAdmin(admin.ModelAdmin):
    list_display = ("issue", "subscription", "recipient_email", "sent_at", "created_at")
    search_fields = ("recipient_email", "issue__repository__full_name", "subscription__email")
    list_filter = ("sent_at", "created_at")
    readonly_fields = (
        "issue",
        "subscription",
        "recipient_email",
        "sent_at",
        "last_error",
        "created_at",
        "updated_at",
    )
