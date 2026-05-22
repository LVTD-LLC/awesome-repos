from django.db import models
from django.urls import reverse
from django.utils import timezone

from apps.core.base_models import BaseModel


class AwesomeList(BaseModel):
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    source_url = models.URLField(unique=True, help_text="GitHub awesome-list repo URL")
    repo_full_name = models.CharField(max_length=255, blank=True, default="")
    description = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)
    last_scanned_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["name"]

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
    raw = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-stars", "full_name"]
        indexes = [
            models.Index(fields=["-stars"]),
            models.Index(fields=["-github_pushed_at"]),
            models.Index(fields=["is_archived"]),
            models.Index(fields=["language"]),
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
