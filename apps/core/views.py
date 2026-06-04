import json
from datetime import timedelta
from urllib.parse import urlsplit, urlunsplit

from allauth.account.internal.flows.email_verification import (
    send_verification_email_to_address,
)
from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView
from django_q.tasks import async_task

from apps.core.analytics import queue_track_event
from apps.core.forms import HighlightedRepoDetailsForm, SponsorAdDetailsForm
from apps.core.models import HighlightedRepoPurchase, Profile, SponsorAdPurchase
from apps.core.payments import (
    StripeConfigurationError,
    StripeRequestError,
    create_ads_checkout_session,
    create_highlighted_repo_checkout_session,
    create_remove_ads_checkout_session,
    highlighted_repo_checkout_configured,
    remove_ads_checkout_configured,
    retrieve_checkout_session,
    stripe_configured,
    verify_webhook_signature,
)
from apps.repos.forms import AwesomeListCreateForm
from apps.repos.models import AwesomeList, RepositoryLike, UserStarredRepository
from apps.repos.services import (
    github_rate_limit_status,
    github_social_token_for_profile,
    github_social_token_is_usable,
    profile_has_github_token,
)
from awesome_repos.utils import get_awesome_repos_logger

logger = get_awesome_repos_logger(__name__)


def _profile_id_for_request(request) -> int | None:
    if request.user.is_authenticated:
        return request.user.profile.id
    return None


def _track_checkout_started(request, *, session_id: str, product: str) -> None:
    queue_track_event(
        event_name="checkout_started",
        profile_id=_profile_id_for_request(request),
        distinct_id=f"stripe_checkout:{session_id}",
        properties={
            "product": product,
            "currency": "usd",
            "transaction_id": session_id,
        },
        source_function=f"{product} checkout",
    )


def _track_purchase_completed(*, session, product: str, profile: Profile | None = None) -> None:
    amount_total = session.get("amount_total") or 0
    currency = (session.get("currency") or "usd").lower()
    session_id = session.get("id", "")
    queue_track_event(
        event_name="purchase_completed",
        profile_id=profile.id if profile else None,
        distinct_id=f"stripe_checkout:{session_id}" if session_id else None,
        properties={
            "product": product,
            "value": amount_total / 100,
            "currency": currency,
            "transaction_id": session_id,
        },
        source_function=f"{product} checkout completion",
    )


def _profile_for_checkout_session(session, expected_user_id=None) -> Profile | None:
    client_reference_id = session.get("client_reference_id")
    if not client_reference_id:
        return None

    try:
        user_id = int(client_reference_id)
    except TypeError, ValueError:
        return None

    if expected_user_id is not None and user_id != expected_user_id:
        return None

    return Profile.objects.filter(user_id=user_id).first()


def _handle_completed_checkout_session(session) -> None:
    metadata = session.get("metadata", {})
    if metadata.get("app") == "awesome" and metadata.get("kind", "sponsor_ads") == "sponsor_ads":
        purchase = upsert_purchase_from_checkout_session(session)
        if purchase.status in {SponsorAdPurchase.Status.PAID, SponsorAdPurchase.Status.ACTIVE}:
            notify_sponsor_payment(purchase)
            _track_purchase_completed(
                session=session,
                product="sponsor_ads",
                profile=_profile_for_checkout_session(session),
            )
    elif metadata.get("app") == "awesome" and metadata.get("kind") == "highlighted_repo":
        purchase = upsert_highlighted_repo_from_checkout_session(session)
        if purchase.status in {
            HighlightedRepoPurchase.Status.PAID,
            HighlightedRepoPurchase.Status.ACTIVE,
        }:
            notify_highlighted_repo_payment(purchase)
            _track_purchase_completed(
                session=session,
                product="highlighted_repo",
                profile=_profile_for_checkout_session(session),
            )
    elif metadata.get("app") == "awesome" and metadata.get("kind") == "remove_ads":
        profile = enable_remove_ads_for_checkout_session(session)
        if profile is not None:
            _track_purchase_completed(session=session, product="remove_ads", profile=profile)


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


def _checkout_email_lines(purchase):
    return [
        "Someone just bought one month of ads on Awesome.",
        "",
        f"Buyer email: {purchase.buyer_email or 'Unknown'}",
        f"Buyer name: {purchase.buyer_name or 'Unknown'}",
        f"Amount: {purchase.amount_total / 100:.2f} {purchase.currency.upper()}",
        f"Stripe checkout session: {purchase.stripe_checkout_session_id}",
        f"Stripe payment intent: {purchase.stripe_payment_intent_id or 'Unknown'}",
        f"Stripe customer: {purchase.stripe_customer_id or 'Unknown'}",
        (
            "Ad details form: "
            f"{build_absolute_public_url(reverse('sponsor_success'))}"
            f"?session_id={purchase.stripe_checkout_session_id}"
        ),
    ]


def notify_sponsor_payment(purchase):
    notification_time = timezone.now()
    updated = SponsorAdPurchase.objects.filter(
        Q(notification_sent_at__isnull=True),
        id=purchase.id,
    ).update(notification_sent_at=notification_time, updated_at=notification_time)
    if not updated:
        return

    try:
        send_mail(
            subject="Awesome ad purchase paid",
            message="\n".join(_checkout_email_lines(purchase)),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[settings.AWESOME_ADS_NOTIFY_EMAIL],
            fail_silently=False,
        )
    except Exception:
        SponsorAdPurchase.objects.filter(id=purchase.id).update(notification_sent_at=None)
        raise

    purchase.notification_sent_at = notification_time


def _highlighted_repo_email_lines(purchase):
    return [
        "Someone just bought a 7-day highlighted repository placement on Awesome.",
        "",
        f"Buyer email: {purchase.buyer_email or 'Unknown'}",
        f"Buyer name: {purchase.buyer_name or 'Unknown'}",
        f"Amount: {purchase.amount_total / 100:.2f} {purchase.currency.upper()}",
        f"Stripe checkout session: {purchase.stripe_checkout_session_id}",
        f"Stripe payment intent: {purchase.stripe_payment_intent_id or 'Unknown'}",
        f"Stripe customer: {purchase.stripe_customer_id or 'Unknown'}",
        (
            "Highlighted repo details form: "
            f"{build_absolute_public_url(reverse('highlighted_repo_success'))}"
            f"?session_id={purchase.stripe_checkout_session_id}"
        ),
    ]


def notify_highlighted_repo_payment(purchase):
    notification_time = timezone.now()
    updated = HighlightedRepoPurchase.objects.filter(
        Q(notification_sent_at__isnull=True),
        id=purchase.id,
    ).update(notification_sent_at=notification_time, updated_at=notification_time)
    if not updated:
        return

    try:
        send_mail(
            subject="Awesome highlighted repo purchase paid",
            message="\n".join(_highlighted_repo_email_lines(purchase)),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[settings.AWESOME_ADS_NOTIFY_EMAIL],
            fail_silently=False,
        )
    except Exception:
        HighlightedRepoPurchase.objects.filter(id=purchase.id).update(notification_sent_at=None)
        raise

    purchase.notification_sent_at = notification_time


def upsert_purchase_from_checkout_session(session):
    purchase, _created = SponsorAdPurchase.objects.get_or_create(
        stripe_checkout_session_id=session["id"]
    )
    purchase.mark_paid_from_checkout_session(session)
    purchase.save()
    return purchase


def upsert_highlighted_repo_from_checkout_session(session):
    purchase, _created = HighlightedRepoPurchase.objects.get_or_create(
        stripe_checkout_session_id=session["id"]
    )
    purchase.mark_paid_from_checkout_session(session)
    purchase.save()
    return purchase


def enable_remove_ads_for_checkout_session(session, expected_user_id=None):
    metadata = session.get("metadata", {})
    if metadata.get("app") != "awesome" or metadata.get("kind") != "remove_ads":
        return None

    if session.get("payment_status") != "paid":
        return None

    profile = _profile_for_checkout_session(session, expected_user_id=expected_user_id)
    if profile is None:
        return None

    if not profile.remove_ads:
        profile.remove_ads = True
        profile.save(update_fields=["remove_ads", "updated_at"])

    return profile


@require_POST
def sponsor_checkout(request):
    if not stripe_configured():
        messages.error(request, "Sponsor checkout is not configured yet.")
        return redirect("repos:search")

    success_url = (
        build_absolute_public_url(reverse("sponsor_success")) + "?session_id={CHECKOUT_SESSION_ID}"
    )
    cancel_url = build_absolute_public_url(reverse("repos:search"))
    try:
        session = create_ads_checkout_session(
            success_url=success_url,
            cancel_url=cancel_url,
            client_reference_id=str(request.user.id) if request.user.is_authenticated else "",
        )
    except (StripeConfigurationError, StripeRequestError) as exc:
        logger.error("Sponsor checkout creation failed", error=str(exc), exc_info=True)
        messages.error(request, "Could not start checkout. Please email rasul@lvtd.dev.")
        return redirect("repos:search")

    SponsorAdPurchase.objects.get_or_create(stripe_checkout_session_id=session["id"])
    _track_checkout_started(
        request,
        session_id=session["id"],
        product="sponsor_ads",
    )
    return redirect(session["url"])


def sponsor_success(request):
    session_id = request.GET.get("session_id") or request.POST.get("session_id")
    if not session_id:
        return HttpResponseBadRequest("Missing checkout session.")

    purchase = get_object_or_404(SponsorAdPurchase, stripe_checkout_session_id=session_id)
    if request.method == "GET" and stripe_configured():
        try:
            purchase = upsert_purchase_from_checkout_session(retrieve_checkout_session(session_id))
            if purchase.status in {SponsorAdPurchase.Status.PAID, SponsorAdPurchase.Status.ACTIVE}:
                try:
                    notify_sponsor_payment(purchase)
                except Exception as exc:
                    logger.error(
                        "Sponsor payment notification failed on success page",
                        session_id=session_id,
                        error=str(exc),
                        exc_info=True,
                    )
        except (StripeConfigurationError, StripeRequestError) as exc:
            logger.warning(
                "Sponsor checkout session refresh failed",
                session_id=session_id,
                error=str(exc),
            )

    if purchase.status == SponsorAdPurchase.Status.CHECKOUT_STARTED:
        return HttpResponseForbidden("Payment is not complete yet.")

    if request.method == "POST":
        form = SponsorAdDetailsForm(request.POST, request.FILES, instance=purchase)
        if form.is_valid():
            purchase = form.save(commit=False)
            purchase.mark_details_submitted()
            purchase.save()
            cache.delete("awesome:active_sponsor_ad")
            queue_track_event(
                event_name="sponsor_ad_details_submitted",
                profile_id=_profile_id_for_request(request),
                distinct_id=f"stripe_checkout:{session_id}",
                properties={
                    "product": "sponsor_ads",
                    "transaction_id": session_id,
                },
                source_function="sponsor_success",
            )
            messages.success(request, "Thanks — your ad details are saved.")
            return redirect("repos:search")
    else:
        form = SponsorAdDetailsForm(instance=purchase)

    return render(
        request,
        "pages/sponsor-success.html",
        {
            "form": form,
            "purchase": purchase,
            "session_id": session_id,
            "amount_dollars": purchase.amount_total / 100,
        },
    )


@require_POST
def highlighted_repo_checkout(request):
    if not highlighted_repo_checkout_configured():
        messages.error(request, "Highlighted repo checkout is not configured yet.")
        return redirect("repos:search")

    success_url = (
        build_absolute_public_url(reverse("highlighted_repo_success"))
        + "?session_id={CHECKOUT_SESSION_ID}"
    )
    cancel_url = build_absolute_public_url(reverse("repos:search"))
    try:
        session = create_highlighted_repo_checkout_session(
            success_url=success_url,
            cancel_url=cancel_url,
            client_reference_id=str(request.user.id) if request.user.is_authenticated else "",
        )
    except (StripeConfigurationError, StripeRequestError) as exc:
        logger.error("Highlighted repo checkout creation failed", error=str(exc), exc_info=True)
        messages.error(request, "Could not start checkout. Please email rasul@lvtd.dev.")
        return redirect("repos:search")

    HighlightedRepoPurchase.objects.get_or_create(stripe_checkout_session_id=session["id"])
    _track_checkout_started(
        request,
        session_id=session["id"],
        product="highlighted_repo",
    )
    return redirect(session["url"])


@login_required
@require_POST
def remove_ads_checkout(request):
    profile, _created = Profile.objects.get_or_create(user=request.user)
    if profile.remove_ads:
        messages.success(request, "Ads are already removed for your account.")
        return redirect("settings")

    if not remove_ads_checkout_configured():
        messages.error(request, "Remove Ads checkout is not configured yet.")
        return redirect("settings")

    success_url = (
        build_absolute_public_url(reverse("settings"))
        + "?remove_ads=success&session_id={CHECKOUT_SESSION_ID}"
    )
    cancel_url = build_absolute_public_url(reverse("settings"))
    try:
        session = create_remove_ads_checkout_session(
            success_url=success_url,
            cancel_url=cancel_url,
            client_reference_id=str(request.user.id),
        )
    except (StripeConfigurationError, StripeRequestError) as exc:
        logger.error("Remove Ads checkout creation failed", error=str(exc), exc_info=True)
        messages.error(request, "Could not start checkout. Please try again shortly.")
        return redirect("settings")

    _track_checkout_started(
        request,
        session_id=session["id"],
        product="remove_ads",
    )
    return redirect(session["url"])


def highlighted_repo_success(request):
    session_id = request.GET.get("session_id") or request.POST.get("session_id")
    if not session_id:
        return HttpResponseBadRequest("Missing checkout session.")

    purchase = get_object_or_404(HighlightedRepoPurchase, stripe_checkout_session_id=session_id)
    if request.method == "GET" and highlighted_repo_checkout_configured():
        try:
            purchase = upsert_highlighted_repo_from_checkout_session(
                retrieve_checkout_session(session_id)
            )
            if purchase.status in {
                HighlightedRepoPurchase.Status.PAID,
                HighlightedRepoPurchase.Status.ACTIVE,
            }:
                try:
                    notify_highlighted_repo_payment(purchase)
                except Exception as exc:
                    logger.error(
                        "Highlighted repo payment notification failed on success page",
                        session_id=session_id,
                        error=str(exc),
                        exc_info=True,
                    )
        except (StripeConfigurationError, StripeRequestError) as exc:
            logger.warning(
                "Highlighted repo checkout session refresh failed",
                session_id=session_id,
                error=str(exc),
            )

    if purchase.status == HighlightedRepoPurchase.Status.CHECKOUT_STARTED:
        return HttpResponseForbidden("Payment is not complete yet.")

    if request.method == "POST":
        form = HighlightedRepoDetailsForm(request.POST, instance=purchase)
        if form.is_valid():
            purchase = form.save(commit=False)
            purchase.mark_details_submitted()
            purchase.save()
            cache.delete("awesome:active_highlighted_repo")
            queue_track_event(
                event_name="highlighted_repo_details_submitted",
                profile_id=_profile_id_for_request(request),
                distinct_id=f"stripe_checkout:{session_id}",
                properties={
                    "product": "highlighted_repo",
                    "transaction_id": session_id,
                },
                source_function="highlighted_repo_success",
            )
            messages.success(request, "Thanks — your highlighted repository details are saved.")
            return redirect("repos:search")
    else:
        form = HighlightedRepoDetailsForm(instance=purchase)

    return render(
        request,
        "pages/highlighted-repo-success.html",
        {
            "form": form,
            "purchase": purchase,
            "session_id": session_id,
            "amount_dollars": purchase.amount_total / 100,
        },
    )


@csrf_exempt
@require_POST
def stripe_webhook(request):
    try:
        signature_is_valid = verify_webhook_signature(
            request.body,
            request.headers.get("Stripe-Signature", ""),
            settings.STRIPE_WEBHOOK_SECRET,
        )
    except StripeConfigurationError:
        logger.error("Stripe webhook secret is not configured")
        return HttpResponseBadRequest("Stripe webhook is not configured.")

    if not signature_is_valid:
        return HttpResponseForbidden("Invalid Stripe signature.")

    try:
        event = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON.")

    if event.get("type") == "checkout.session.completed":
        session = event.get("data", {}).get("object", {})
        _handle_completed_checkout_session(session)

    return HttpResponse("ok")


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


class UserSettingsView(LoginRequiredMixin, TemplateView):
    login_url = "account_login"
    template_name = "pages/user-settings.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        profile, _created = Profile.objects.get_or_create(user=user)
        github_social_token = github_social_token_for_profile(profile)
        github_connected = github_social_token_is_usable(github_social_token)
        github_account = github_social_token.account if github_connected else None
        github_account_data = (github_account.extra_data or {}) if github_account else {}
        github_login = github_account_data.get("login", "")

        email_address = EmailAddress.objects.filter(user=user, email__iexact=user.email).first()
        context["email_verified"] = bool(email_address and email_address.verified)
        context["resend_confirmation_url"] = reverse("resend_confirmation")
        context["github_auth_enabled"] = "github" in settings.SOCIALACCOUNT_PROVIDERS
        context["github_connected"] = github_connected
        context["github_login"] = github_login
        context["github_profile_url"] = github_account_data.get("html_url", "")
        context["github_starred_count"] = UserStarredRepository.objects.filter(
            profile=profile
        ).count()
        context["github_starred_import_enabled"] = profile.github_starred_repos_import_enabled
        context["github_starred_last_imported_at"] = profile.github_starred_repos_last_imported_at
        context["github_starred_last_error"] = profile.github_starred_repos_last_error
        context["liked_repository_count"] = RepositoryLike.objects.filter(user=user).count()
        context["remove_ads_enabled"] = profile.remove_ads
        context["remove_ads_checkout_configured"] = remove_ads_checkout_configured()
        if self.request.GET.get("remove_ads") == "success":
            session_id = self.request.GET.get("session_id", "")
            if session_id and remove_ads_checkout_configured():
                try:
                    enable_remove_ads_for_checkout_session(
                        retrieve_checkout_session(session_id),
                        expected_user_id=user.id,
                    )
                    profile.refresh_from_db()
                    context["remove_ads_enabled"] = profile.remove_ads
                except (StripeConfigurationError, StripeRequestError) as exc:
                    logger.warning(
                        "Remove Ads checkout session refresh failed",
                        session_id=session_id,
                        error=str(exc),
                    )

            if profile.remove_ads:
                messages.success(self.request, "Ads are removed for your account.")
            else:
                messages.success(
                    self.request,
                    "Thanks — your payment is processing. "
                    "Ads will disappear once Stripe confirms it.",
                )

        return context


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
    queue_track_event(
        event_name="starred_import_requested",
        profile_id=profile.id,
        properties={
            "was_import_enabled": was_import_enabled,
            "refresh_existing": True,
        },
        source_function="import_starred_repositories",
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
    queue_track_event(
        event_name="starred_import_disabled",
        profile_id=profile.id,
        properties={},
        source_function="disable_starred_repository_import",
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

        recent_users = (
            User.objects.select_related("profile")
            .annotate(starred_repository_count=Count("profile__starred_repository_links"))
            .order_by("-date_joined")[:10]
        )
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
