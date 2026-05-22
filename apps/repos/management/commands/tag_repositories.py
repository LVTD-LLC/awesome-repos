from django.core.management.base import BaseCommand, CommandError

from apps.repos.models import Repository
from apps.repos.tags import (
    build_repository_tagging_payload,
    repository_tagging_configured,
    repository_tags_are_current,
    save_repository_tags,
)


class Command(BaseCommand):
    help = "Build or refresh generated repository tags from saved descriptions and READMEs."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None, help="Limit repositories processed")
        parser.add_argument(
            "--force",
            action="store_true",
            help="Regenerate tags even when the stored source hash matches",
        )

    def handle(self, *args, **options):
        if not repository_tagging_configured():
            raise CommandError(
                "Repository tagging is not configured: ensure REPOSITORY_TAGGING_ENABLED=True "
                "and the provider API key is set."
            )

        queryset = Repository.objects.order_by("full_name")
        if options["limit"]:
            queryset = queryset[: options["limit"]]

        tagged = 0
        skipped = 0
        unchanged = 0
        failures = []
        for repository in queryset:
            try:
                payload = build_repository_tagging_payload(repository, repository.readme)
                if payload is None:
                    skipped += 1
                    continue

                if not options["force"] and repository_tags_are_current(repository, payload):
                    unchanged += 1
                    continue

                tags = save_repository_tags(
                    repository,
                    repository.readme,
                    force=options["force"],
                )
            except Exception as exc:  # noqa: BLE001 - report and continue batch backfills
                failures.append({"repo": repository.full_name, "error": str(exc)})
                self.stderr.write(self.style.ERROR(f"{repository.full_name}: {exc}"))
                continue

            if not tags:
                skipped += 1
                continue
            tagged += 1

        result = {
            "tagged": tagged,
            "skipped": skipped,
            "unchanged": unchanged,
            "failure_count": len(failures),
            "failures": failures[:25],
        }
        self.stdout.write(self.style.SUCCESS(str(result)))
