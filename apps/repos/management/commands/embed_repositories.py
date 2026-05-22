from django.core.management.base import BaseCommand, CommandError

from apps.repos.embeddings import (
    build_repository_embedding_payload,
    repository_embedding_is_current,
    repository_embeddings_configured,
    save_repository_embedding,
)
from apps.repos.models import Repository
from apps.repos.services import fetch_repository_readme


class Command(BaseCommand):
    help = "Build or refresh pgvector embeddings for GitHub repository descriptions and READMEs."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None, help="Limit repositories processed")
        parser.add_argument(
            "--force",
            action="store_true",
            help="Rebuild embeddings even when the stored source hash matches",
        )

    def handle(self, *args, **options):
        if not repository_embeddings_configured():
            raise CommandError("OPENROUTER_API_KEY is required to build repository embeddings.")

        queryset = Repository.objects.order_by("full_name")
        if options["limit"]:
            queryset = queryset[: options["limit"]]

        embedded = 0
        skipped = 0
        unchanged = 0
        failures = []
        for repository in queryset:
            try:
                readme_text = fetch_repository_readme(repository.full_name)
                payload = build_repository_embedding_payload(repository, readme_text)
                if payload is None:
                    skipped += 1
                    continue

                if not options["force"] and repository_embedding_is_current(repository, payload):
                    unchanged += 1
                    continue

                embedding = save_repository_embedding(
                    repository,
                    readme_text,
                    force=options["force"],
                )
            except Exception as exc:  # noqa: BLE001 - report and continue batch backfills
                failures.append({"repo": repository.full_name, "error": str(exc)})
                self.stderr.write(self.style.ERROR(f"{repository.full_name}: {exc}"))
                continue

            if embedding is None:
                skipped += 1
                continue
            embedded += 1

        result = {
            "embedded": embedded,
            "skipped": skipped,
            "unchanged": unchanged,
            "failure_count": len(failures),
            "failures": failures[:25],
        }
        self.stdout.write(self.style.SUCCESS(str(result)))
