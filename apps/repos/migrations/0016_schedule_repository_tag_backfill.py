from datetime import timedelta

from django.db import migrations
from django.utils import timezone

SCHEDULE_NAME = "Daily repository generated-tag backfill"
SCHEDULE_FUNC = "apps.repos.tasks.tag_repositories_task"


def next_daily_run():
    now = timezone.now()
    next_run = now.replace(hour=4, minute=0, second=0, microsecond=0)
    if next_run <= now:
        next_run += timedelta(days=1)
    return next_run


def create_repository_tag_backfill_schedule(apps, schema_editor):
    Schedule = apps.get_model("django_q", "Schedule")
    Schedule.objects.update_or_create(
        name=SCHEDULE_NAME,
        defaults={
            "func": SCHEDULE_FUNC,
            "schedule_type": "D",
            "repeats": -1,
            "next_run": next_daily_run(),
        },
    )


def remove_repository_tag_backfill_schedule(apps, schema_editor):
    Schedule = apps.get_model("django_q", "Schedule")
    Schedule.objects.filter(name=SCHEDULE_NAME, func=SCHEDULE_FUNC).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("django_q", "0019_alter_task_options_alter_ormq_key_alter_ormq_lock_and_more"),
        ("repos", "0015_repositorylike"),
    ]

    operations = [
        migrations.RunPython(
            create_repository_tag_backfill_schedule,
            remove_repository_tag_backfill_schedule,
        ),
    ]
