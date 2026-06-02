from allauth.account.forms import LoginForm, SignupForm
from django import forms

from apps.core.models import HighlightedRepoPurchase, Profile, SponsorAdPurchase
from apps.core.utils import DivErrorList


class CustomSignUpForm(SignupForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.error_class = DivErrorList


class CustomLoginForm(LoginForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.error_class = DivErrorList


class SponsorAdDetailsForm(forms.ModelForm):
    startup_name = forms.CharField(
        max_length=120,
        required=True,
        widget=forms.TextInput(attrs={"placeholder": "Acme AI", "class": "app-input"}),
    )
    short_description = forms.CharField(
        max_length=180,
        required=True,
        widget=forms.TextInput(
            attrs={
                "placeholder": "The fastest way to ship reliable agent workflows.",
                "class": "app-input",
                "maxlength": 180,
            }
        ),
    )
    logo = forms.ImageField(required=True)

    class Meta:
        model = SponsorAdPurchase
        fields = ["logo", "startup_name", "short_description"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["logo"].required = not bool(self.instance and self.instance.logo)

    def clean_logo(self):
        logo = self.cleaned_data.get("logo")
        if logo and logo.size > 512 * 1024:
            raise forms.ValidationError("Upload a small logo under 512KB.")
        return logo


class HighlightedRepoDetailsForm(forms.ModelForm):
    repo_full_name = forms.CharField(
        max_length=255,
        required=True,
        widget=forms.TextInput(attrs={"placeholder": "owner/repository", "class": "app-input"}),
    )
    repo_url = forms.URLField(
        max_length=500,
        required=True,
        widget=forms.URLInput(
            attrs={"placeholder": "https://github.com/owner/repository", "class": "app-input"}
        ),
    )
    short_description = forms.CharField(
        max_length=220,
        required=True,
        widget=forms.TextInput(
            attrs={
                "placeholder": "A short reason developers should check out this repository.",
                "class": "app-input",
                "maxlength": 220,
            }
        ),
    )

    class Meta:
        model = HighlightedRepoPurchase
        fields = ["repo_full_name", "repo_url", "short_description"]

    def clean_repo_full_name(self):
        repo_full_name = self.cleaned_data["repo_full_name"].strip()
        if "/" not in repo_full_name:
            raise forms.ValidationError("Use the GitHub owner/repository format.")
        return repo_full_name


class ProfileUpdateForm(forms.ModelForm):
    first_name = forms.CharField(max_length=30)
    last_name = forms.CharField(max_length=30)
    email = forms.EmailField()

    class Meta:
        model = Profile
        fields = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.user:
            self.fields["first_name"].initial = self.instance.user.first_name
            self.fields["last_name"].initial = self.instance.user.last_name
            self.fields["email"].initial = self.instance.user.email

    def save(self, commit=True):
        profile = super().save(commit=False)
        user = profile.user
        user.first_name = self.cleaned_data["first_name"]
        user.last_name = self.cleaned_data["last_name"]
        user.email = self.cleaned_data["email"]
        if commit:
            user.save()
            profile.save()
        return profile
