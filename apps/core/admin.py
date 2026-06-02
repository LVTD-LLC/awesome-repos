from django.contrib import admin

from apps.core.models import EmailSent, HighlightedRepoPurchase, SponsorAdPurchase

admin.site.register(EmailSent)
admin.site.register(SponsorAdPurchase)
admin.site.register(HighlightedRepoPurchase)
