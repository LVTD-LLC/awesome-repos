from django.core.management.base import BaseCommand, CommandError

from apps.repos.tags import (
    repository_tagging_configured,
    tag_repository_batch,
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

        result = tag_repository_batch(limit=options["limit"], force=options["force"])
        for failure in result["failures"]:
            self.stderr.write(self.style.ERROR(f"{failure['repo']}: {failure['error']}"))
        self.stdout.write(self.style.SUCCESS(str(result)))
