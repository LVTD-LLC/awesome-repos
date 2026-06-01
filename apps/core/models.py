from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone
from django_q.tasks import async_task

from apps.core.base_models import BaseModel
from apps.core.choices import EmailType, ProfileStates
from apps.core.model_utils import (
    generate_api_key,
    get_api_key_prefix,
    hash_api_key,
    verify_api_key,
)
from awesome_repos.utils import get_awesome_repos_logger

logger = get_awesome_repos_logger(__name__)


class Profile(BaseModel):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    api_key_prefix = models.CharField(
        max_length=32,
        unique=True,
        null=True,
        blank=True,
        default=None,
    )
    api_key_hash = models.CharField(max_length=128, blank=True, default="")
    github_starred_repos_import_enabled = models.BooleanField(default=False)
    github_starred_repos_last_imported_at = models.DateTimeField(null=True, blank=True)
    github_starred_repos_last_error = models.TextField(blank=True, default="")

    state = models.CharField(
        max_length=255,
        choices=ProfileStates.choices,
        default=ProfileStates.STRANGER,
        help_text="The current state of the user's profile",
    )

    def track_state_change(self, to_state, metadata=None, source_function=None):
        async_task(
            "apps.core.tasks.track_state_change",
            profile_id=self.id,
            from_state=self.current_state,
            to_state=to_state,
            metadata=metadata,
            source_function=source_function,
            group="Track State Change",
        )

    @property
    def current_state(self):
        if not self.state_transitions.all().exists():
            return ProfileStates.STRANGER
        latest_transition = self.state_transitions.latest("created_at")
        return latest_transition.to_state

    @property
    def has_api_key(self):
        return bool(self.api_key_hash and self.api_key_prefix)

    def set_api_key(self, api_key=None):
        api_key = api_key or generate_api_key()
        api_key_prefix = get_api_key_prefix(api_key)
        if not api_key_prefix:
            raise ValueError("API keys must include a public prefix and secret.")

        self.api_key_prefix = api_key_prefix
        self.api_key_hash = hash_api_key(api_key)
        return api_key

    def rotate_api_key(self):
        api_key = self.set_api_key()
        self.save(update_fields=["api_key_prefix", "api_key_hash", "updated_at"])
        return api_key

    def check_api_key(self, api_key):
        api_key_prefix = get_api_key_prefix(api_key)
        if not api_key_prefix or api_key_prefix != self.api_key_prefix or not self.api_key_hash:
            return False

        return verify_api_key(api_key, self.api_key_hash)


class ProfileStateTransition(BaseModel):
    profile = models.ForeignKey(
        Profile,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="state_transitions",
    )
    from_state = models.CharField(max_length=255, choices=ProfileStates.choices)
    to_state = models.CharField(max_length=255, choices=ProfileStates.choices)
    backup_profile_id = models.IntegerField()
    metadata = models.JSONField(null=True, blank=True)


class SponsorAdPurchase(BaseModel):
    class Status(models.TextChoices):
        CHECKOUT_STARTED = "checkout_started", "Checkout started"
        PAID = "paid", "Paid"
        ACTIVE = "active", "Active"

    stripe_checkout_session_id = models.CharField(max_length=255, unique=True)
    stripe_payment_intent_id = models.CharField(max_length=255, blank=True, default="")
    stripe_customer_id = models.CharField(max_length=255, blank=True, default="")
    amount_total = models.PositiveIntegerField(default=0)
    currency = models.CharField(max_length=8, blank=True, default="usd")
    buyer_email = models.EmailField(blank=True, default="")
    buyer_name = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.CHECKOUT_STARTED,
    )
    notification_sent_at = models.DateTimeField(null=True, blank=True)
    details_submitted_at = models.DateTimeField(null=True, blank=True)
    logo = models.ImageField(upload_to="sponsor-ads/logos/", blank=True, null=True)
    startup_name = models.CharField(max_length=120, blank=True, default="")
    short_description = models.CharField(max_length=180, blank=True, default="")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.startup_name or self.buyer_email or self.stripe_checkout_session_id

    @property
    def logo_url(self):
        if self.logo:
            return self.logo.url
        return ""

    def mark_paid_from_checkout_session(self, session):
        customer = session.get("customer") or {}
        customer_details = session.get("customer_details") or {}
        payment_intent = session.get("payment_intent") or ""
        if isinstance(payment_intent, dict):
            self.stripe_payment_intent_id = payment_intent.get("id", "")
        else:
            self.stripe_payment_intent_id = payment_intent or ""
        if isinstance(customer, dict):
            self.stripe_customer_id = customer.get("id", "")
        else:
            self.stripe_customer_id = customer or ""
        self.amount_total = session.get("amount_total") or self.amount_total
        self.currency = session.get("currency") or self.currency
        self.buyer_email = (
            customer_details.get("email") or session.get("customer_email") or self.buyer_email
        )
        self.buyer_name = customer_details.get("name") or self.buyer_name
        if session.get("payment_status") == "paid" and self.status == self.Status.CHECKOUT_STARTED:
            self.status = self.Status.PAID

    def mark_details_submitted(self):
        self.status = self.Status.ACTIVE
        self.details_submitted_at = timezone.now()


class EmailSent(BaseModel):
    email_address = models.EmailField(help_text="The recipient email address")
    email_type = models.CharField(
        max_length=50, choices=EmailType.choices, help_text="Type of email sent"
    )
    profile = models.ForeignKey(
        Profile,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="emails_sent",
        help_text="Associated user profile, if applicable",
    )

    class Meta:
        verbose_name = "Email Sent"
        verbose_name_plural = "Emails Sent"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.email_type} to {self.email_address}"
