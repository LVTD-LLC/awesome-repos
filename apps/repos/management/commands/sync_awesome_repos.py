from django.core.management.base import BaseCommand

from apps.repos.models import AwesomeList
from apps.repos.services import refresh_repositories, sync_awesome_list


class Command(BaseCommand):
    help = "Sync configured awesome lists and/or repository metadata from GitHub."

    def add_arguments(self, parser):
        parser.add_argument("--list", dest="list_slug", help="Only sync one awesome list slug")
        parser.add_argument("--limit", type=int, default=None, help="Limit repos per awesome list")
        parser.add_argument(
            "--refresh",
            action="store_true",
            help="Refresh existing repositories only",
        )

    def handle(self, *args, **options):
        if options["refresh"]:
            result = refresh_repositories(limit=options["limit"])
            self.stdout.write(self.style.SUCCESS(str(result)))
            return

        lists = AwesomeList.objects.filter(is_active=True)
        if options["list_slug"]:
            lists = lists.filter(slug=options["list_slug"])
        for awesome_list in lists:
            result = sync_awesome_list(awesome_list, limit=options["limit"])
            self.stdout.write(self.style.SUCCESS(str(result)))
