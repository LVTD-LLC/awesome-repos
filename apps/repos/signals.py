from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.repos.cache import clear_public_repository_filter_options_cache
from apps.repos.models import AwesomeList, AwesomeListItem, Repository


@receiver([post_save, post_delete], sender=AwesomeList)
@receiver([post_save, post_delete], sender=AwesomeListItem)
@receiver([post_save, post_delete], sender=Repository)
def invalidate_public_repository_filter_options_cache(**kwargs):
    clear_public_repository_filter_options_cache()
