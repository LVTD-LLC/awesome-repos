from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.urls import reverse
from django.utils import timezone
from pgvector.django import HnswIndex, VectorField

from apps.core.base_models import BaseModel

REPOSITORY_EMBEDDING_DIMENSIONS = 1536


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
    is_active = models.BooleanField(default=True)
    last_scanned_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")
    raw = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["-stars"]),
            models.Index(fields=["-github_pushed_at"]),
            models.Index(fields=["-last_scanned_at"]),
        ]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("repos:list_detail", args=[self.slug])


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
    open_issues = models.PositiveIntegerField(default=0)
    watchers = models.PositiveIntegerField(default=0)
    default_branch = models.CharField(max_length=255, blank=True, default="")
    is_archived = models.BooleanField(default=False)
    is_disabled = models.BooleanField(default=False)
    is_fork = models.BooleanField(default=False)
    github_created_at = models.DateTimeField(null=True, blank=True)
    github_updated_at = models.DateTimeField(null=True, blank=True)
    github_pushed_at = models.DateTimeField(null=True, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    readme = models.TextField(blank=True, default="")
    readme_path = models.CharField(max_length=255, blank=True, default="")
    readme_url = models.URLField(max_length=500, blank=True, default="")
    readme_synced_at = models.DateTimeField(null=True, blank=True)
    readme_last_error = models.TextField(blank=True, default="")
    uses_ai_for_development = models.BooleanField(default=False)
    ai_development_signals = models.JSONField(default=list, blank=True)
    generated_tags = models.JSONField(default=list, blank=True)
    generated_tags_model = models.CharField(max_length=255, blank=True, default="")
    generated_tags_source_hash = models.CharField(max_length=64, blank=True, default="")
    generated_tags_synced_at = models.DateTimeField(null=True, blank=True)
    generated_tags_last_error = models.TextField(blank=True, default="")
    raw = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-stars", "full_name"]
        indexes = [
            models.Index(fields=["-stars"]),
            models.Index(fields=["-github_pushed_at"]),
            models.Index(fields=["is_archived"]),
            models.Index(fields=["language"]),
            models.Index(fields=["uses_ai_for_development"]),
            GinIndex(fields=["topics"], name="repo_topics_gin_idx"),
            GinIndex(fields=["generated_tags"], name="repo_gen_tags_gin_idx"),
        ]

    def __str__(self):
        return self.full_name

    @property
    def awesome_list_count(self):
        return self.awesome_items.count()

    def get_absolute_url(self):
        return reverse("repos:repo_detail", kwargs={"owner": self.owner, "name": self.name})


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
    open_issues = models.PositiveIntegerField(default=0)
    watchers = models.PositiveIntegerField(default=0)
    default_branch = models.CharField(max_length=255, blank=True, default="")
    is_archived = models.BooleanField(default=False)
    is_disabled = models.BooleanField(default=False)
    is_fork = models.BooleanField(default=False)
    github_created_at = models.DateTimeField(null=True, blank=True)
    github_updated_at = models.DateTimeField(null=True, blank=True)
    github_pushed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-captured_at", "-id"]
        indexes = [
            models.Index(fields=["repository", "-captured_at"]),
            models.Index(fields=["-captured_at"]),
        ]

    def __str__(self):
        return f"{self.repository.full_name} at {self.captured_at:%Y-%m-%d %H:%M}"


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
