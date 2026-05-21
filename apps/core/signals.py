from allauth.account.signals import email_confirmed, user_signed_up
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver
from django_q.tasks import async_task


from apps.core.models import Profile, ProfileStates
from awesome_repos.utils import get_awesome_repos_logger

logger = get_awesome_repos_logger(__name__)


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        profile = Profile.objects.create(user=instance)
        profile.track_state_change(
            to_state=ProfileStates.SIGNED_UP,
            source_function="create_user_profile signal",
        )

    if instance.id == 1:
        # Use update() to avoid triggering the signal again
        User.objects.filter(id=1).update(is_staff=True, is_superuser=True)





