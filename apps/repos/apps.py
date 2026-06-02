from django.apps import AppConfig


class ReposConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.repos"
    verbose_name = "Repositories"

    def ready(self):
        import apps.repos.signals  # noqa: F401
