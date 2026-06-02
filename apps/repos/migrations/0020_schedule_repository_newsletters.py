from datetime import timedelta

from django.db import migrations
from django.utils import timezone

SCHEDULES = [
    {
        "name": "Daily repository newsletter commit polling",
        "func": "apps.repos.tasks.poll_tracked_repositories_task",
        "schedule_type": "D",
        "anchor": "daily",
        "hour": 2,
        "minute": 30,
    },
    {
        "name": "Daily repository newsletter commit summaries",
        "func": "apps.repos.tasks.summarize_newsletter_commits_task",
        "schedule_type": "D",
        "anchor": "daily",
        "hour": 3,
        "minute": 15,
    },
    {
        "name": "Weekly repository newsletters",
        "func": "apps.repos.tasks.generate_weekly_newsletters_task",
        "schedule_type": "W",
        "anchor": "monday",
        "hour": 4,
        "minute": 15,
    },
    {
        "name": "Monthly repository newsletters",
        "func": "apps.repos.tasks.generate_monthly_newsletters_task",
        "schedule_type": "M",
        "anchor": "month",
        "hour": 4,
        "minute": 45,
    },
]


def next_daily_run(hour, minute):
    now = timezone.now()
    run_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if run_at <= now:
        run_at += timedelta(days=1)
    return run_at


def next_monday_run(hour, minute):
    now = timezone.now()
    days_until_monday = (7 - now.weekday()) % 7
    run_at = (now + timedelta(days=days_until_monday)).replace(
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
    )
    if run_at <= now:
        run_at += timedelta(days=7)
    return run_at


def next_month_run(hour, minute):
    now = timezone.now()
    year = now.year + int(now.month == 12)
    month = 1 if now.month == 12 else now.month + 1
    return now.replace(
        year=year,
        month=month,
        day=1,
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
    )


def next_schedule_run(schedule):
    if schedule["anchor"] == "monday":
        return next_monday_run(schedule["hour"], schedule["minute"])
    if schedule["anchor"] == "month":
        return next_month_run(schedule["hour"], schedule["minute"])
    return next_daily_run(schedule["hour"], schedule["minute"])


def create_repository_newsletter_schedules(apps, schema_editor):
    Schedule = apps.get_model("django_q", "Schedule")
    for schedule in SCHEDULES:
        Schedule.objects.update_or_create(
            name=schedule["name"],
            defaults={
                "func": schedule["func"],
                "schedule_type": schedule["schedule_type"],
                "repeats": -1,
                "next_run": next_schedule_run(schedule),
            },
        )


def remove_repository_newsletter_schedules(apps, schema_editor):
    Schedule = apps.get_model("django_q", "Schedule")
    for schedule in SCHEDULES:
        Schedule.objects.filter(name=schedule["name"], func=schedule["func"]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("django_q", "0019_alter_task_options_alter_ormq_key_alter_ormq_lock_and_more"),
        ("repos", "0019_newsletterissuedelivery_newslettersubscription_and_more"),
        ("repos", "0019_repository_dependency_ecosystems_and_more"),
    ]

    operations = [
        migrations.RunPython(
            create_repository_newsletter_schedules,
            remove_repository_newsletter_schedules,
        ),
    ]
