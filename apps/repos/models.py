import secrets

from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.urls import reverse
from django.utils import timezone
from pgvector.django import HnswIndex, VectorField

from apps.core.base_models import BaseModel

REPOSITORY_EMBEDDING_DIMENSIONS = 1536


def generate_newsletter_token():
    return secrets.token_urlsafe(32)


class NewsletterCadence(models.TextChoices):
    WEEKLY = "weekly", "Weekly"
    MONTHLY = "monthly", "Monthly"


class AwesomeList(BaseModel):
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    source_url = models.URLField(unique=True, help_text="GitHub awesome-list repo URL")
    repo_full_name = models.CharField(max_length=255, blank=True, default="")
    description = models.TextField(blank=True, default="")
    topics = models.JSONField(default=list, blank=True)
    stars = models.PositiveIntegerField(default=0)
    forks = models.PositiveIntegerField(default=0)
    open_issues = models.PositiveIntegerField(default=0)
    watchers = models.PositiveIntegerField(default=0)
    commits_count = models.PositiveIntegerField(null=True, blank=True)
    readme_repository_count = models.PositiveIntegerField(default=0)
    default_branch = models.CharField(max_length=255, blank=True, default="")
    is_archived = models.BooleanField(default=False)
    is_disabled = models.BooleanField(default=False)
    github_created_at = models.DateTimeField(null=True, blank=True)
    github_updated_at = models.DateTimeField(null=True, blank=True)
    github_pushed_at = models.DateTimeField(null=True, blank=True)
    first_commit_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    last_scanned_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")
    raw = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["-stars"]),
            models.Index(fields=["-github_pushed_at"]),
            models.Index(fields=["first_commit_at"]),
            models.Index(fields=["-last_scanned_at"]),
        ]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("repos:list_detail", args=[self.slug])

    def sync_from_source(self, limit: int | None = None) -> dict:
        from apps.repos.services import sync_awesome_list

        return sync_awesome_list(self, limit=limit)

    def discover_missing_repositories_from_source(self, limit: int | None = None) -> dict:
        from apps.repos.services import discover_missing_awesome_list_repositories

        return discover_missing_awesome_list_repositories(self, limit=limit)


class AwesomeListSnapshot(BaseModel):
    awesome_list = models.ForeignKey(
        AwesomeList,
        on_delete=models.CASCADE,
        related_name="snapshots",
    )
    captured_at = models.DateTimeField(default=timezone.now)
    source = models.CharField(max_length=50, default="github_api")
    repo_full_name = models.CharField(max_length=255, blank=True, default="")
    description = models.TextField(blank=True, default="")
    topics = models.JSONField(default=list, blank=True)
    stars = models.PositiveIntegerField(default=0)
    forks = models.PositiveIntegerField(default=0)
    open_issues = models.PositiveIntegerField(default=0)
    watchers = models.PositiveIntegerField(default=0)
    commits_count = models.PositiveIntegerField(null=True, blank=True)
    readme_repository_count = models.PositiveIntegerField(default=0)
    default_branch = models.CharField(max_length=255, blank=True, default="")
    is_archived = models.BooleanField(default=False)
    is_disabled = models.BooleanField(default=False)
    github_created_at = models.DateTimeField(null=True, blank=True)
    github_updated_at = models.DateTimeField(null=True, blank=True)
    github_pushed_at = models.DateTimeField(null=True, blank=True)
    first_commit_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-captured_at", "-id"]
        indexes = [
            models.Index(fields=["awesome_list", "-captured_at"]),
            models.Index(fields=["-captured_at"]),
        ]

    def __str__(self):
        list_label = self.repo_full_name or f"awesome list {self.awesome_list_id}"
        return f"{list_label} at {self.captured_at:%Y-%m-%d %H:%M}"


class AwesomeListRequest(BaseModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ADDED = "added", "Added"
        DECLINED = "declined", "Declined"

    source_url = models.URLField(help_text="GitHub awesome-list repo URL")
    repo_full_name = models.CharField(max_length=255, unique=True)
    requester_email = models.EmailField(blank=True, default="")
    note = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=20,
        choices=Status,
        default=Status.PENDING,
        db_index=True,
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewer_notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.repo_full_name} ({self.get_status_display()})"


class Repository(BaseModel):
    host = models.CharField(max_length=50, default="github")
    full_name = models.CharField(max_length=255, unique=True)
    owner = models.CharField(max_length=255)
    name = models.CharField(max_length=255)
    url = models.URLField(unique=True)
    description = models.TextField(blank=True, default="")
    homepage_url = models.URLField(blank=True, default="")
    language = models.CharField(max_length=100, blank=True, default="")
    license_name = models.CharField(max_length=255, blank=True, default="")
    topics = models.JSONField(default=list, blank=True)
    stars = models.PositiveIntegerField(default=0)
    forks = models.PositiveIntegerField(default=0)
    commit_count = models.PositiveIntegerField(null=True, blank=True)
    open_issues = models.PositiveIntegerField(default=0)
    watchers = models.PositiveIntegerField(default=0)
    default_branch = models.CharField(max_length=255, blank=True, default="")
    is_archived = models.BooleanField(default=False)
    is_disabled = models.BooleanField(default=False)
    is_fork = models.BooleanField(default=False)
    github_created_at = models.DateTimeField(null=True, blank=True)
    github_updated_at = models.DateTimeField(null=True, blank=True)
    github_pushed_at = models.DateTimeField(null=True, blank=True)
    first_commit_at = models.DateTimeField(null=True, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    readme = models.TextField(blank=True, default="")
    readme_path = models.CharField(max_length=255, blank=True, default="")
    readme_url = models.URLField(max_length=500, blank=True, default="")
    readme_synced_at = models.DateTimeField(null=True, blank=True)
    readme_last_error = models.TextField(blank=True, default="")
    dependency_files = models.JSONField(default=list, blank=True)
    dependency_ecosystems = models.JSONField(default=list, blank=True)
    package_managers = models.JSONField(default=list, blank=True)
    detected_stacks = models.JSONField(default=list, blank=True)
    stack_signals = models.JSONField(default=list, blank=True)
    stack_detected_at = models.DateTimeField(null=True, blank=True)
    stack_detection_last_error = models.TextField(blank=True, default="")
    uses_ai_for_development = models.BooleanField(default=False)
    ai_development_signals = models.JSONField(default=list, blank=True)
    is_awesome_list_candidate = models.BooleanField(default=False)
    awesome_list_detected_repo_count = models.PositiveIntegerField(default=0)
    awesome_list_detection_reasons = models.JSONField(default=list, blank=True)
    generated_tags = models.JSONField(default=list, blank=True)
    generated_tags_model = models.CharField(max_length=255, blank=True, default="")
    generated_tags_source_hash = models.CharField(max_length=64, blank=True, default="")
    generated_tags_synced_at = models.DateTimeField(null=True, blank=True)
    generated_tags_last_error = models.TextField(blank=True, default="")
    raw = models.JSONField(default=dict, blank=True)
    newsletter_tracking_enabled = models.BooleanField(default=False)
    newsletter_tracking_started_at = models.DateTimeField(null=True, blank=True)
    newsletter_tracking_last_polled_at = models.DateTimeField(null=True, blank=True)
    newsletter_tracking_last_error = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-stars", "full_name"]
        indexes = [
            models.Index(fields=["-stars"]),
            models.Index(fields=["-github_pushed_at"]),
            models.Index(fields=["first_commit_at"]),
            models.Index(fields=["is_archived"]),
            models.Index(fields=["language"]),
            models.Index(fields=["uses_ai_for_development"]),
            models.Index(fields=["is_awesome_list_candidate"], name="repo_is_awesome_list_idx"),
            models.Index(fields=["newsletter_tracking_enabled", "full_name"]),
            GinIndex(fields=["topics"], name="repo_topics_gin_idx"),
            GinIndex(fields=["generated_tags"], name="repo_gen_tags_gin_idx"),
            GinIndex(fields=["dependency_ecosystems"], name="repo_dep_ecosystems_gin_idx"),
            GinIndex(fields=["package_managers"], name="repo_package_mgrs_gin_idx"),
            GinIndex(fields=["detected_stacks"], name="repo_detected_stacks_gin_idx"),
        ]

    def __str__(self):
        return self.full_name

    @property
    def awesome_list_count(self):
        return self.awesome_items.count()

    def get_absolute_url(self):
        return reverse("repos:repo_detail", kwargs={"owner": self.owner, "name": self.name})

    @classmethod
    def sync_from_source(
        cls,
        full_name: str,
        *,
        github_access_token: str | None = None,
    ) -> Repository:
        from apps.repos.services import upsert_repository_from_github

        return upsert_repository_from_github(full_name, github_access_token=github_access_token)


class RepositorySnapshot(BaseModel):
    repository = models.ForeignKey(Repository, on_delete=models.CASCADE, related_name="snapshots")
    captured_at = models.DateTimeField(default=timezone.now)
    source = models.CharField(max_length=50, default="github_api")
    description = models.TextField(blank=True, default="")
    homepage_url = models.URLField(blank=True, default="")
    language = models.CharField(max_length=100, blank=True, default="")
    license_name = models.CharField(max_length=255, blank=True, default="")
    topics = models.JSONField(default=list, blank=True)
    stars = models.PositiveIntegerField(default=0)
    forks = models.PositiveIntegerField(default=0)
    commit_count = models.PositiveIntegerField(null=True, blank=True)
    open_issues = models.PositiveIntegerField(default=0)
    watchers = models.PositiveIntegerField(default=0)
    default_branch = models.CharField(max_length=255, blank=True, default="")
    is_archived = models.BooleanField(default=False)
    is_disabled = models.BooleanField(default=False)
    is_fork = models.BooleanField(default=False)
    github_created_at = models.DateTimeField(null=True, blank=True)
    github_updated_at = models.DateTimeField(null=True, blank=True)
    github_pushed_at = models.DateTimeField(null=True, blank=True)
    first_commit_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-captured_at", "-id"]
        indexes = [
            models.Index(fields=["repository", "-captured_at"]),
            models.Index(fields=["-captured_at"]),
        ]

    def __str__(self):
        return f"{self.repository.full_name} at {self.captured_at:%Y-%m-%d %H:%M}"


class UserStarredRepository(BaseModel):
    profile = models.ForeignKey(
        "core.Profile",
        on_delete=models.CASCADE,
        related_name="starred_repository_links",
    )
    repository = models.ForeignKey(
        Repository,
        on_delete=models.CASCADE,
        related_name="starred_by_profiles",
    )
    starred_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(default=timezone.now)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    sync_error = models.TextField(blank=True, default="")

    class Meta:
        unique_together = ("profile", "repository")
        ordering = [models.F("starred_at").desc(nulls_last=True), "repository__full_name"]
        indexes = [
            models.Index(fields=["profile", "-last_seen_at"]),
            models.Index(fields=["repository", "-last_synced_at"]),
        ]

    def __str__(self):
        return f"{self.profile_id}: {self.repository.full_name}"


class AwesomeListItem(BaseModel):
    awesome_list = models.ForeignKey(AwesomeList, on_delete=models.CASCADE, related_name="items")
    repository = models.ForeignKey(
        Repository, on_delete=models.CASCADE, related_name="awesome_items"
    )
    first_seen_in_scan_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("awesome_list", "repository")
        ordering = ["awesome_list__name", "repository__full_name"]

    def __str__(self):
        return f"{self.repository.full_name} in {self.awesome_list.name}"


class RepositoryLike(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="repository_likes",
    )
    repository = models.ForeignKey(
        Repository,
        on_delete=models.CASCADE,
        related_name="likes",
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "repository"],
                name="unique_repository_like",
            )
        ]
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["repository", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.user} likes {self.repository.full_name}"


class RepositoryEmbedding(BaseModel):
    repository = models.OneToOneField(
        Repository,
        on_delete=models.CASCADE,
        related_name="vector",
    )
    model = models.CharField(max_length=255)
    dimensions = models.PositiveSmallIntegerField(default=REPOSITORY_EMBEDDING_DIMENSIONS)
    source_text_hash = models.CharField(max_length=64)
    source_text_chars = models.PositiveIntegerField(default=0)
    embedding = VectorField(dimensions=REPOSITORY_EMBEDDING_DIMENSIONS)
    embedded_at = models.DateTimeField()

    class Meta:
        ordering = ["repository__full_name"]
        indexes = [
            models.Index(fields=["model", "source_text_hash"], name="repo_emb_model_hash_idx"),
            HnswIndex(
                name="repo_emb_vec_hnsw_idx",
                fields=["embedding"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            ),
        ]

    def __str__(self):
        return f"{self.repository.full_name} embedding"


class NewsletterSubscription(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="newsletter_subscriptions",
    )
    repository = models.ForeignKey(
        Repository,
        on_delete=models.CASCADE,
        related_name="newsletter_subscriptions",
    )
    email = models.EmailField()
    cadence = models.CharField(
        max_length=20,
        choices=NewsletterCadence.choices,
        default=NewsletterCadence.WEEKLY,
        db_index=True,
    )
    is_active = models.BooleanField(default=True)
    unsubscribed_at = models.DateTimeField(null=True, blank=True)
    unsubscribe_token = models.CharField(
        max_length=64,
        unique=True,
        default=generate_newsletter_token,
    )

    class Meta:
        ordering = ["repository__full_name", "user_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "repository"],
                condition=models.Q(is_active=True),
                name="unique_active_newsletter_subscription",
            )
        ]
        indexes = [
            models.Index(fields=["repository", "cadence", "is_active"]),
            models.Index(fields=["user", "is_active"]),
        ]

    def __str__(self):
        return f"{self.email} -> {self.repository.full_name} ({self.cadence})"

    def unsubscribe_url(self):
        return reverse("repos:newsletter_unsubscribe", args=[self.unsubscribe_token])


class RepositoryCommit(BaseModel):
    repository = models.ForeignKey(
        Repository,
        on_delete=models.CASCADE,
        related_name="newsletter_commits",
    )
    sha = models.CharField(max_length=64)
    branch = models.CharField(max_length=255, blank=True, default="")
    message = models.TextField(blank=True, default="")
    html_url = models.URLField(max_length=500, blank=True, default="")
    api_url = models.URLField(max_length=500, blank=True, default="")
    author_name = models.CharField(max_length=255, blank=True, default="")
    author_email = models.EmailField(blank=True, default="")
    author_login = models.CharField(max_length=255, blank=True, default="")
    authored_at = models.DateTimeField(null=True, blank=True)
    committer_name = models.CharField(max_length=255, blank=True, default="")
    committer_email = models.EmailField(blank=True, default="")
    committer_login = models.CharField(max_length=255, blank=True, default="")
    committed_at = models.DateTimeField(null=True, blank=True)
    parent_shas = models.JSONField(default=list, blank=True)
    additions = models.PositiveIntegerField(default=0)
    deletions = models.PositiveIntegerField(default=0)
    changed_files = models.PositiveIntegerField(default=0)
    files = models.JSONField(default=list, blank=True)
    patch_truncated = models.BooleanField(default=False)
    raw_metadata = models.JSONField(default=dict, blank=True)
    summary = models.TextField(blank=True, default="")
    summary_model = models.CharField(max_length=255, blank=True, default="")
    summary_source_hash = models.CharField(max_length=64, blank=True, default="")
    summarized_at = models.DateTimeField(null=True, blank=True)
    summary_last_error = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-committed_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["repository", "sha"],
                name="unique_repository_newsletter_commit",
            )
        ]
        indexes = [
            models.Index(fields=["repository", "-committed_at"]),
            models.Index(fields=["branch", "-committed_at"]),
            models.Index(fields=["summarized_at"]),
        ]

    def __str__(self):
        return f"{self.repository.full_name}@{self.sha[:12]}"


class RepositoryNewsletterIssue(BaseModel):
    repository = models.ForeignKey(
        Repository,
        on_delete=models.CASCADE,
        related_name="newsletter_issues",
    )
    cadence = models.CharField(
        max_length=20,
        choices=NewsletterCadence.choices,
        db_index=True,
    )
    period_start = models.DateField()
    period_end = models.DateField()
    slug = models.SlugField(max_length=120)
    title = models.CharField(max_length=255)
    content_markdown = models.TextField(blank=True, default="")
    content_html = models.TextField(blank=True, default="")
    commit_count = models.PositiveIntegerField(default=0)
    published_at = models.DateTimeField(null=True, blank=True)
    generation_model = models.CharField(max_length=255, blank=True, default="")
    generation_source_hash = models.CharField(max_length=64, blank=True, default="")
    generated_at = models.DateTimeField(null=True, blank=True)
    generation_last_error = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-period_start", "repository__full_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["repository", "cadence", "period_start"],
                name="unique_repository_newsletter_issue_period",
            ),
            models.UniqueConstraint(
                fields=["repository", "cadence", "slug"],
                name="unique_repository_newsletter_issue_slug",
            ),
        ]
        indexes = [
            models.Index(fields=["repository", "cadence", "-period_start"]),
            models.Index(fields=["published_at"]),
        ]

    def __str__(self):
        return f"{self.repository.full_name} {self.cadence} {self.slug}"

    def get_absolute_url(self):
        return reverse(
            "repos:newsletter_issue_detail",
            kwargs={
                "owner": self.repository.owner,
                "name": self.repository.name,
                "cadence": self.cadence,
                "slug": self.slug,
            },
        )


class NewsletterIssueDelivery(BaseModel):
    issue = models.ForeignKey(
        RepositoryNewsletterIssue,
        on_delete=models.CASCADE,
        related_name="deliveries",
    )
    subscription = models.ForeignKey(
        NewsletterSubscription,
        on_delete=models.CASCADE,
        related_name="deliveries",
    )
    recipient_email = models.EmailField()
    sent_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["issue", "subscription"],
                name="unique_newsletter_issue_delivery",
            )
        ]
        indexes = [
            models.Index(fields=["sent_at"]),
            models.Index(fields=["recipient_email", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.issue} -> {self.recipient_email}"
