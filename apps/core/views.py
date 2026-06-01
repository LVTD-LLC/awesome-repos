from datetime import timedelta
from urllib.parse import urlsplit, urlunsplit

from allauth.account.internal.flows.email_verification import (
    send_verification_email_to_address,
)
from allauth.account.models import EmailAddress
from allauth.mfa.models import Authenticator
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.models import User
from django.contrib.messages.views import SuccessMessageMixin
from django.db import transaction
from django.db.models import Count
from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView, UpdateView
from django_q.tasks import async_task

from apps.core.forms import ProfileUpdateForm
from apps.core.models import Profile
from apps.repos.forms import AwesomeListCreateForm
from apps.repos.models import AwesomeList, UserStarredRepository
from apps.repos.services import github_rate_limit_status, profile_has_github_token
from awesome_repos.utils import get_awesome_repos_logger

logger = get_awesome_repos_logger(__name__)
NEW_API_KEY_SESSION_KEY = "new_api_key"


def build_absolute_public_url(path: str) -> str:
    """Build a public URL from SITE_URL and upgrade non-local HTTP origins to HTTPS."""
    base_url = settings.SITE_URL.rstrip("/")
    parsed = urlsplit(base_url)
    hostname = parsed.hostname or ""
    is_local = hostname in {"localhost", "127.0.0.1", "0.0.0.0", "::1"} or hostname.endswith(
        ".localhost"
    )

    if parsed.scheme == "http" and not is_local:
        parsed = parsed._replace(scheme="https")
        base_url = urlunsplit(parsed).rstrip("/")

    return f"{base_url}/{path.lstrip('/')}"


class HomeView(LoginRequiredMixin, TemplateView):
    login_url = "account_login"
    template_name = "pages/home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile, _created = Profile.objects.get_or_create(user=self.request.user)
        context["starred_repository_count"] = UserStarredRepository.objects.filter(
            profile=profile
        ).count()
        context["github_starred_import_enabled"] = profile.github_starred_repos_import_enabled
        context["github_starred_last_imported_at"] = profile.github_starred_repos_last_imported_at

        return context


class UserSettingsView(LoginRequiredMixin, SuccessMessageMixin, UpdateView):
    login_url = "account_login"
    model = Profile
    form_class = ProfileUpdateForm
    success_message = "User Profile Updated"
    success_url = reverse_lazy("settings")
    template_name = "pages/user-settings.html"

    def get_object(self):
        profile, _created = Profile.objects.get_or_create(user=self.request.user)
        return profile

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        profile = self.object

        email_address = EmailAddress.objects.filter(user=user, email__iexact=user.email).first()
        context["email_verified"] = bool(email_address and email_address.verified)
        context["resend_confirmation_url"] = reverse("resend_confirmation")
        context["passkey_count"] = Authenticator.objects.filter(
            user=user,
            type=Authenticator.Type.WEBAUTHN,
        ).count()
        context["has_recovery_codes"] = Authenticator.objects.filter(
            user=user,
            type=Authenticator.Type.RECOVERY_CODES,
        ).exists()

        context["api_key_prefix"] = profile.api_key_prefix
        context["has_api_key"] = profile.has_api_key
        context["new_api_key"] = self.request.session.pop(NEW_API_KEY_SESSION_KEY, "")
        context["github_auth_enabled"] = "github" in settings.SOCIALACCOUNT_PROVIDERS
        context["github_connected"] = profile_has_github_token(profile)
        context["github_starred_count"] = UserStarredRepository.objects.filter(
            profile=profile
        ).count()
        context["github_starred_import_enabled"] = profile.github_starred_repos_import_enabled
        context["github_starred_last_imported_at"] = profile.github_starred_repos_last_imported_at
        context["github_starred_last_error"] = profile.github_starred_repos_last_error

        return context


@login_required
@require_POST
def rotate_api_key(request):
    profile, _created = Profile.objects.get_or_create(user=request.user)
    api_key = profile.rotate_api_key()
    request.session[NEW_API_KEY_SESSION_KEY] = api_key
    messages.success(request, "New API key generated. Copy it now; it will only be shown once.")
    return redirect("settings")


@login_required
@require_POST
def import_starred_repositories(request):
    profile, _created = Profile.objects.get_or_create(user=request.user)
    if not profile_has_github_token(profile):
        messages.error(
            request,
            "Connect GitHub before importing starred repositories.",
        )
        return redirect("settings")

    was_import_enabled = profile.github_starred_repos_import_enabled
    profile.github_starred_repos_import_enabled = True
    profile.github_starred_repos_last_error = ""
    profile.save(
        update_fields=[
            "github_starred_repos_import_enabled",
            "github_starred_repos_last_error",
            "updated_at",
        ]
    )
    transaction.on_commit(
        lambda: async_task(
            "apps.repos.tasks.import_starred_repositories_task",
            profile.id,
            refresh_existing=True,
            group="Import GitHub starred repositories",
        )
    )
    success_message = (
        "Queued your GitHub starred repository refresh."
        if was_import_enabled
        else "Enabled daily GitHub starred repository refresh and queued your first import."
    )
    messages.success(
        request,
        success_message,
    )
    return redirect("settings")


@login_required
@require_POST
def disable_starred_repository_import(request):
    profile, _created = Profile.objects.get_or_create(user=request.user)
    profile.github_starred_repos_import_enabled = False
    profile.github_starred_repos_last_error = ""
    profile.save(
        update_fields=[
            "github_starred_repos_import_enabled",
            "github_starred_repos_last_error",
            "updated_at",
        ]
    )
    messages.success(request, "Disabled daily GitHub starred repository refresh.")
    return redirect("settings")


@login_required
@require_POST
def resend_confirmation_email(request):
    user = request.user

    try:
        email_address = EmailAddress.objects.filter(user=user, email__iexact=user.email).first()

        if not email_address:
            messages.error(request, "No email address found for your account.")
            logger.warning(
                "[Resend Confirmation] No email address found",
                user_id=user.id,
                user_email=user.email,
            )
            return redirect("settings")

        if email_address.verified:
            messages.info(request, "Your email is already verified.")
            logger.info(
                "[Resend Confirmation] Email already verified",
                user_id=user.id,
                user_email=user.email,
            )
            return redirect("settings")

        sent = send_verification_email_to_address(request, email_address, signup=False)
        if not sent:
            messages.error(
                request,
                "Please wait before requesting another confirmation email.",
            )
            return redirect("settings")
        logger.info(
            "[Resend Confirmation] Email sent successfully",
            user_id=user.id,
            user_email=user.email,
        )
        if settings.ACCOUNT_EMAIL_VERIFICATION_BY_CODE_ENABLED:
            return redirect("account_email_verification_sent")

    except Exception as e:
        messages.error(request, "Failed to send confirmation email. Please try again later.")
        logger.error(
            "[Resend Confirmation] Failed to send email",
            user_id=user.id,
            user_email=user.email,
            error=str(e),
            exc_info=True,
        )

    return redirect("settings")


@login_required
@require_POST
def delete_account(request):
    """Permanently delete the current user and all related data.

    Safety: requires a confirmation text value.
    """

    confirmation = request.POST.get("confirmation", "")
    if confirmation != "DELETE":
        messages.error(request, "Type DELETE to confirm account deletion.")
        return redirect("settings")

    user_id = request.user.id

    # Ensure we log the user out and remove data in a single flow.
    with transaction.atomic():
        user = request.user
        logout(request)
        user.delete()

    logger.info("User account deleted", user_id=user_id)
    return redirect(f"{reverse('repos:search')}?account_deleted=1")


class AdminPanelView(UserPassesTestMixin, TemplateView):
    template_name = "pages/admin-panel.html"
    login_url = "account_login"
    scan_task_group = "Scan awesome list"

    def test_func(self):
        return self.request.user.is_superuser

    def handle_no_permission(self):
        messages.error(self.request, "You don't have permission to access this page.")
        return redirect("home")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        now = timezone.now()
        week_ago = now - timedelta(days=7)
        month_ago = now - timedelta(days=30)

        total_users = User.objects.count()
        total_profiles = Profile.objects.count()
        new_users_week = User.objects.filter(date_joined__gte=week_ago).count()
        new_users_month = User.objects.filter(date_joined__gte=month_ago).count()

        recent_users = User.objects.select_related("profile").order_by("-date_joined")[:10]
        recent_awesome_lists = AwesomeList.objects.annotate(item_count=Count("items")).order_by(
            "-last_scanned_at",
            "name",
        )[:10]

        # Calculate average users per day for last 30 days
        avg_users_per_day = new_users_month / 30 if new_users_month > 0 else 0

        context.update(
            {
                "total_users": total_users,
                "total_profiles": total_profiles,
                "new_users_week": new_users_week,
                "new_users_month": new_users_month,
                "recent_users": recent_users,
                "avg_users_per_day": avg_users_per_day,
                "awesome_list_form": kwargs.get("awesome_list_form") or AwesomeListCreateForm(),
                "recent_awesome_lists": recent_awesome_lists,
                "github_rate_limit": github_rate_limit_status(),
            }
        )

        logger.info(
            "Admin panel accessed",
            email=self.request.user.email,
            profile_id=self.request.user.profile.id,
        )

        return context

    def queue_awesome_list_scan(self, awesome_list, *, is_retry=False):
        transaction.on_commit(
            lambda: async_task(
                "apps.repos.tasks.sync_awesome_list_task",
                awesome_list.id,
                group=self.scan_task_group,
            )
        )
        logger.info(
            "Admin queued awesome-list scan",
            awesome_list_id=awesome_list.id,
            awesome_list_slug=awesome_list.slug,
            source_url=awesome_list.source_url,
            is_retry=is_retry,
        )

    def retry_awesome_list_scan(self, request):
        try:
            awesome_list_id = int(request.POST.get("awesome_list_id", ""))
        except TypeError, ValueError:
            messages.error(request, "Choose an awesome list to retry.")
            return redirect("admin_panel")

        awesome_list = AwesomeList.objects.filter(id=awesome_list_id).first()
        if awesome_list is None:
            messages.error(request, "That awesome list could not be found.")
            return redirect("admin_panel")

        self.queue_awesome_list_scan(awesome_list, is_retry=True)
        messages.success(request, f"Queued a retry scan for {awesome_list.name}.")
        return redirect("admin_panel")

    def post(self, request, *args, **kwargs):
        if request.POST.get("action") == "retry_awesome_list":
            return self.retry_awesome_list_scan(request)

        form = AwesomeListCreateForm(request.POST)
        if form.is_valid():
            awesome_list = form.save()
            self.queue_awesome_list_scan(awesome_list)
            messages.success(
                request,
                f"Added {awesome_list.name} and queued a scan.",
            )
            return redirect("admin_panel")

        context = self.get_context_data(awesome_list_form=form)
        return self.render_to_response(context)
