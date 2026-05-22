from datetime import timedelta

from django.db import migrations
from django.utils import timezone

OLD_SCHEDULE_NAME = "Monthly repository metadata refresh"
NEW_SCHEDULE_NAME = "Daily budgeted repository metadata refresh"
SCHEDULE_FUNC = "apps.repos.tasks.refresh_repositories_task"


def next_daily_run():
    now = timezone.now()
    next_run = now.replace(hour=3, minute=0, second=0, microsecond=0)
    if next_run <= now:
        next_run += timedelta(days=1)
    return next_run


def next_monthly_run():
    now = timezone.now()
    year = now.year + int(now.month == 12)
    month = 1 if now.month == 12 else now.month + 1
    return now.replace(
        year=year,
        month=month,
        day=1,
        hour=3,
        minute=0,
        second=0,
        microsecond=0,
    )


def create_daily_budgeted_repository_refresh_schedule(apps, schema_editor):
    Schedule = apps.get_model("django_q", "Schedule")
    Schedule.objects.filter(name=OLD_SCHEDULE_NAME, func=SCHEDULE_FUNC).delete()
    Schedule.objects.update_or_create(
        name=NEW_SCHEDULE_NAME,
        defaults={
            "func": SCHEDULE_FUNC,
            "schedule_type": "D",
            "repeats": -1,
            "next_run": next_daily_run(),
        },
    )


def restore_monthly_repository_refresh_schedule(apps, schema_editor):
    Schedule = apps.get_model("django_q", "Schedule")
    Schedule.objects.filter(name=NEW_SCHEDULE_NAME, func=SCHEDULE_FUNC).delete()
    Schedule.objects.update_or_create(
        name=OLD_SCHEDULE_NAME,
        defaults={
            "func": SCHEDULE_FUNC,
            "schedule_type": "M",
            "repeats": -1,
            "next_run": next_monthly_run(),
        },
    )


class Migration(migrations.Migration):
    dependencies = [
        ("repos", "0007_repository_generated_tags_and_more"),
    ]

    operations = [
        migrations.RunPython(
            create_daily_budgeted_repository_refresh_schedule,
            restore_monthly_repository_refresh_schedule,
        ),
    ]
