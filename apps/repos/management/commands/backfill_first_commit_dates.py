from django.core.management.base import BaseCommand

from apps.repos.models import AwesomeList, Repository
from apps.repos.services import fetch_github_commit_count_and_first_commit_at, parse_github_repo_url


class Command(BaseCommand):
    help = "Backfill first-commit dates for existing awesome lists and repositories from GitHub."

    def add_arguments(self, parser):
        parser.add_argument(
            "--kind",
            choices=("all", "lists", "repositories"),
            default="all",
            help="Which records to backfill.",
        )
        parser.add_argument("--limit", type=int, default=None, help="Limit records processed")
        parser.add_argument(
            "--force",
            action="store_true",
            help="Refresh records even when first_commit_at is already set.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Fetch dates but do not save changes.",
        )

    def handle(self, *args, **options):
        results = {}
        limit = options["limit"]
        if options["kind"] in {"all", "lists"}:
            results["lists"] = self.backfill_awesome_lists(
                limit=limit,
                force=options["force"],
                dry_run=options["dry_run"],
            )
        if options["kind"] in {"all", "repositories"}:
            results["repositories"] = self.backfill_repositories(
                limit=limit,
                force=options["force"],
                dry_run=options["dry_run"],
            )
        self.stdout.write(self.style.SUCCESS(str(results)))

    def backfill_awesome_lists(self, *, limit: int | None, force: bool, dry_run: bool) -> dict:
        queryset = AwesomeList.objects.filter(is_active=True).order_by("name")
        if not force:
            queryset = queryset.filter(first_commit_at__isnull=True)
        if limit:
            queryset = queryset[:limit]
        return self.backfill_records(queryset, force=force, dry_run=dry_run, is_list=True)

    def backfill_repositories(self, *, limit: int | None, force: bool, dry_run: bool) -> dict:
        queryset = Repository.objects.order_by("full_name")
        if not force:
            queryset = queryset.filter(first_commit_at__isnull=True)
        if limit:
            queryset = queryset[:limit]
        return self.backfill_records(queryset, force=force, dry_run=dry_run, is_list=False)

    def backfill_records(self, queryset, *, force: bool, dry_run: bool, is_list: bool) -> dict:
        updated = 0
        skipped = 0
        failures = []
        for item in queryset.iterator():
            full_name = self.record_full_name(item, is_list=is_list)
            default_branch = item.default_branch
            if not full_name or not default_branch:
                skipped += 1
                continue

            try:
                commit_count, first_commit_at = fetch_github_commit_count_and_first_commit_at(
                    full_name,
                    default_branch,
                )
            except Exception as exc:  # noqa: BLE001 - continue through batch backfills
                failures.append({"record": full_name, "error": str(exc)})
                self.stderr.write(self.style.ERROR(f"{full_name}: {exc}"))
                continue

            if first_commit_at is None:
                skipped += 1
                continue

            if not dry_run:
                item.first_commit_at = first_commit_at
                update_fields = ["first_commit_at", "updated_at"]
                if commit_count is not None:
                    if is_list:
                        item.commits_count = commit_count
                        update_fields.append("commits_count")
                    else:
                        item.commit_count = commit_count
                        update_fields.append("commit_count")
                item.save(update_fields=update_fields)
            updated += 1

        return {
            "updated": updated,
            "skipped": skipped,
            "failure_count": len(failures),
            "failures": failures[:25],
            "dry_run": dry_run,
            "force": force,
        }

    def record_full_name(self, item, *, is_list: bool) -> str:
        if not is_list:
            return item.full_name
        if item.repo_full_name:
            return item.repo_full_name
        try:
            return parse_github_repo_url(item.source_url)
        except ValueError:
            return ""
