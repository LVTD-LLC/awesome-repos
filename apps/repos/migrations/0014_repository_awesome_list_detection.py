import re

from django.db import migrations, models

GITHUB_REPO_RE = re.compile(
    r"https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)(?:[/#?][^\s)\]>'\"]*)?",
    re.IGNORECASE,
)
SKIP_REPO_NAMES = {"stargazers", "network", "issues", "pulls", "pull", "wiki", "releases"}
AWESOME_LIST_MIN_REPOSITORY_LINKS = 3
AWESOME_LIST_TOPIC_MARKERS = {"awesome-list", "awesome-lists"}
AWESOME_LIST_TITLE_RE = re.compile(
    r"^\s{0,3}#{1,2}\s+awesome(?:[\s:,-]|$)",
    re.IGNORECASE | re.MULTILINE,
)
AWESOME_LIST_DESCRIPTION_RE = re.compile(
    r"\b(awesome[-\s]+list|curated\s+(?:list|collection)|"
    r"collection\s+of\s+(?:awesome\s+)?(?:projects|resources|repositories))\b",
    re.IGNORECASE,
)


def extract_github_repos(markdown):
    repos = set()
    for owner, repo in GITHUB_REPO_RE.findall(markdown or ""):
        repo = repo.removesuffix(".git")
        if repo.lower() in SKIP_REPO_NAMES:
            continue
        if owner.lower() in {"topics", "collections", "marketplace", "features"}:
            continue
        repos.add(f"{owner}/{repo}")
    return sorted(repos, key=str.lower)


def normalize_repository_tag(value):
    return str(value).strip().lower().replace("_", "-").replace(" ", "-")


def detect_awesome_list_candidate(repository, tracked_source_names):
    topics = {normalize_repository_tag(topic) for topic in repository.topics or []}
    detected_repo_count = len(extract_github_repos(repository.readme or ""))
    has_link_list = detected_repo_count >= AWESOME_LIST_MIN_REPOSITORY_LINKS
    has_awesome_title = bool(AWESOME_LIST_TITLE_RE.search(repository.readme or ""))
    repo_name = (repository.name or repository.full_name.rsplit("/", 1)[-1]).lower()
    has_awesome_name = repo_name == "awesome" or repo_name.startswith(("awesome-", "awesome_"))
    has_awesome_description = bool(AWESOME_LIST_DESCRIPTION_RE.search(repository.description or ""))

    reasons = []
    if repository.full_name.lower() in tracked_source_names:
        reasons.append("tracked_awesome_list_source")
    if topics & AWESOME_LIST_TOPIC_MARKERS:
        reasons.append("github_topic_awesome_list")
    if "awesome" in topics and has_link_list:
        reasons.append("github_topic_awesome")
    if has_awesome_title and has_link_list:
        reasons.append("awesome_readme_title")
    if has_awesome_name and (has_awesome_title or has_link_list):
        reasons.append("awesome_repo_name")
    if has_awesome_description and has_link_list:
        reasons.append("awesome_description")

    return detected_repo_count, reasons


def backfill_awesome_list_candidates(apps, schema_editor):
    AwesomeList = apps.get_model("repos", "AwesomeList")
    Repository = apps.get_model("repos", "Repository")
    tracked_source_names = {
        full_name.lower()
        for full_name in AwesomeList.objects.filter(is_active=True)
        .exclude(repo_full_name="")
        .values_list("repo_full_name", flat=True)
    }
    repositories_to_update = []
    for repository in Repository.objects.only(
        "id",
        "full_name",
        "name",
        "description",
        "topics",
        "readme",
    ).iterator(chunk_size=500):
        detected_repo_count, reasons = detect_awesome_list_candidate(
            repository,
            tracked_source_names,
        )
        if not reasons:
            continue
        repository.is_awesome_list_candidate = True
        repository.awesome_list_detected_repo_count = detected_repo_count
        repository.awesome_list_detection_reasons = reasons
        repositories_to_update.append(repository)

    if repositories_to_update:
        Repository.objects.bulk_update(
            repositories_to_update,
            [
                "is_awesome_list_candidate",
                "awesome_list_detected_repo_count",
                "awesome_list_detection_reasons",
            ],
            batch_size=500,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("repos", "0013_awesomelist_first_commit_at_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="repository",
            name="awesome_list_detected_repo_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="repository",
            name="awesome_list_detection_reasons",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="repository",
            name="is_awesome_list_candidate",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(
            backfill_awesome_list_candidates,
            migrations.RunPython.noop,
        ),
        migrations.AddIndex(
            model_name="repository",
            index=models.Index(
                fields=["is_awesome_list_candidate"], name="repo_is_awesome_list_idx"
            ),
        ),
    ]
