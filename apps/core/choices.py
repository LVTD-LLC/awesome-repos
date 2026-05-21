from django.db import models

class ProfileStates(models.TextChoices):
    STRANGER = "stranger"
    SIGNED_UP = "signed_up"
    FREE = "free" # This can be used for Freemium apps, and will be set when core action is completed
    
    ACCOUNT_DELETED = "account_deleted"


class EmailType(models.TextChoices):
    EMAIL_CONFIRMATION = "EMAIL_CONFIRMATION", "Email Confirmation"
    WELCOME = "WELCOME", "Welcome"
    FEEDBACK_NOTIFICATION = "FEEDBACK_NOTIFICATION", "Feedback Notification"
