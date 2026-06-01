from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("repos", "0016_schedule_repository_tag_backfill"),
        ("repos", "0017_schedule_daily_starred_repository_import"),
    ]

    operations = []
