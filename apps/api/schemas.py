from datetime import datetime
from typing import Any

from ninja import Schema


class ProfileSettingsOut(Schema):
    pass


class UserSettingsOut(Schema):
    profile: ProfileSettingsOut


class UserProfileOut(Schema):
    id: int
    state: str
    has_active_subscription: bool


class UserInfoOut(Schema):
    id: int
    email: str
    username: str
    first_name: str
    last_name: str
    full_name: str
    date_joined: datetime
    profile: UserProfileOut


class PaginationOut(Schema):
    count: int
    page: int
    page_size: int
    num_pages: int
    has_next: bool
    has_previous: bool


class ValueCountOut(Schema):
    name: str
    count: int


class AwesomeListReferenceOut(Schema):
    id: int
    name: str
    slug: str
    source_url: str
    repo_full_name: str
    stars: int


class AwesomeListSummaryOut(Schema):
    id: int
    name: str
    slug: str
    source_url: str
    repo_full_name: str
    description: str
    topics: list[str]
    stars: int
    forks: int
    commits_count: int | None
    open_issues: int
    watchers: int
    readme_repository_count: int
    indexed_repo_count: int
    default_branch: str
    is_archived: bool
    is_disabled: bool
    is_active: bool
    github_created_at: datetime | None
    github_updated_at: datetime | None
    github_pushed_at: datetime | None
    first_commit_at: datetime | None
    last_scanned_at: datetime | None
    last_error: str


class AwesomeListDirectoryTotalsOut(Schema):
    total_lists: int
    total_readme_repositories: int
    total_list_stars: int
    latest_scan: datetime | None
    total_indexed_links: int


class AwesomeListSearchOut(Schema):
    pagination: PaginationOut
    totals: AwesomeListDirectoryTotalsOut
    results: list[AwesomeListSummaryOut]


class AwesomeListRepositoryStatsOut(Schema):
    total_stars: int
    total_forks: int
    active_count: int
    archived_count: int
    latest_repo_push: datetime | None


class AwesomeListDetailOut(Schema):
    awesome_list: AwesomeListSummaryOut
    repo_stats: AwesomeListRepositoryStatsOut
    language_counts: list[ValueCountOut]


class RepositorySnapshotOut(Schema):
    captured_at: datetime
    source: str
    stars: int
    forks: int
    commit_count: int | None
    open_issues: int
    watchers: int
    first_commit_at: datetime | None


class RepositoryHistoryPointOut(Schema):
    captured_at: str
    stars: int
    commit_count: int | None


class RepositoryPerformanceOut(Schema):
    has_history: bool
    snapshot_count: int
    first_snapshot: RepositorySnapshotOut | None
    latest_snapshot: RepositorySnapshotOut | None
    previous_snapshot: RepositorySnapshotOut | None
    stars_since_first: int
    forks_since_first: int
    watchers_since_first: int
    stars_since_previous: int | None
    commits_since_first: int | None
    commits_since_previous: int | None


class RepositorySummaryOut(Schema):
    id: int
    host: str
    full_name: str
    owner: str
    name: str
    url: str
    description: str
    homepage_url: str
    language: str
    license_name: str
    topics: list[str]
    generated_tags: list[str]
    dependency_ecosystems: list[str]
    package_managers: list[str]
    detected_stacks: list[str]
    stack_signals: list[dict[str, Any]]
    stars: int
    forks: int
    commit_count: int | None
    open_issues: int
    watchers: int
    default_branch: str
    is_archived: bool
    is_disabled: bool
    is_fork: bool
    uses_ai_for_development: bool
    is_awesome_list_candidate: bool
    awesome_list_detected_repo_count: int
    awesome_list_detection_reasons: list[str]
    awesome_count: int
    snapshot_count: int | None
    stars_since_first: int | None
    commits_since_first: int | None
    stars_since_recent: int | None
    commits_since_recent: int | None
    stars_growth_percent: float | None
    commits_growth_percent: float | None
    github_created_at: datetime | None
    github_updated_at: datetime | None
    github_pushed_at: datetime | None
    first_commit_at: datetime | None
    last_synced_at: datetime | None
    stack_detected_at: datetime | None
    awesome_lists: list[AwesomeListReferenceOut]


class RepositorySearchOut(Schema):
    pagination: PaginationOut
    results: list[RepositorySummaryOut]


class RepositoryDetailOut(RepositorySummaryOut):
    readme: str
    readme_path: str
    readme_url: str
    readme_synced_at: datetime | None
    readme_last_error: str
    dependency_files: list[dict[str, Any]]
    stack_detection_last_error: str
    ai_development_signals: list[dict[str, Any]]
    performance: RepositoryPerformanceOut
    history: list[RepositoryHistoryPointOut]
    similar_repositories: list[RepositorySummaryOut]


class AwesomeListCreateIn(Schema):
    source_url: str
    queue_scan: bool = True


class AwesomeListMutationOut(Schema):
    queued: bool
    message: str
    awesome_list: AwesomeListSummaryOut


class QueuedTaskOut(Schema):
    queued: bool
    message: str
    task: str
