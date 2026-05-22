from django.db import migrations
from django.utils import timezone

SCHEDULE_NAME = "Monthly repository metadata refresh"
SCHEDULE_FUNC = "apps.repos.tasks.refresh_repositories_task"
MONTHLY = "M"


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


def create_monthly_repository_refresh_schedule(apps, schema_editor):
    Schedule = apps.get_model("django_q", "Schedule")
    Schedule.objects.update_or_create(
        name=SCHEDULE_NAME,
        defaults={
            "func": SCHEDULE_FUNC,
            "schedule_type": MONTHLY,
            "repeats": -1,
            "next_run": next_monthly_run(),
        },
    )


def remove_monthly_repository_refresh_schedule(apps, schema_editor):
    Schedule = apps.get_model("django_q", "Schedule")
    Schedule.objects.filter(name=SCHEDULE_NAME, func=SCHEDULE_FUNC).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("django_q", "0019_alter_task_options_alter_ormq_key_alter_ormq_lock_and_more"),
        ("repos", "0002_schedule_daily_missing_repo_sync"),
    ]

    operations = [
        migrations.RunPython(
            create_monthly_repository_refresh_schedule,
            remove_monthly_repository_refresh_schedule,
        ),
    ]
