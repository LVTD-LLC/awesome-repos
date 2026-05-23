from django import forms
from django.db.models import Q
from django.utils.text import slugify

from apps.repos.models import AwesomeList, AwesomeListRequest
from apps.repos.services import parse_github_repo_url


class AwesomeListCreateForm(forms.Form):
    source_url = forms.URLField(label="GitHub awesome-list URL")

    def clean_source_url(self):
        source_url = self.cleaned_data["source_url"].strip()

        try:
            self.repo_full_name = parse_github_repo_url(source_url)
        except ValueError as exc:
            raise forms.ValidationError(str(exc)) from exc

        if AwesomeList.objects.filter(source_url__iexact=source_url).exists():
            raise forms.ValidationError("That awesome-list URL is already added.")

        return source_url

    def _default_name(self):
        repo_name = getattr(self, "repo_full_name", "").split("/", 1)[-1]
        return repo_name.replace("-", " ").replace("_", " ").title()

    def _unique_slug(self, value: str) -> str:
        base_slug = slugify(value) or "awesome-list"
        slug = base_slug
        suffix = 2

        while AwesomeList.objects.filter(slug=slug).exists():
            slug = f"{base_slug}-{suffix}"
            suffix += 1

        return slug

    def save(self):
        if not self.is_valid():
            raise ValueError("Cannot save an invalid awesome-list form.")

        repo_full_name = getattr(self, "repo_full_name", "")

        return AwesomeList.objects.create(
            name=self._default_name(),
            slug=self._unique_slug(repo_full_name.split("/", 1)[-1]),
            source_url=self.cleaned_data["source_url"],
            repo_full_name=repo_full_name,
        )


class AwesomeListRequestForm(forms.ModelForm):
    class Meta:
        model = AwesomeListRequest
        fields = ["source_url", "requester_email", "note"]

    def clean_source_url(self):
        source_url = self.cleaned_data["source_url"].strip()

        try:
            self.repo_full_name = parse_github_repo_url(source_url)
        except ValueError as exc:
            raise forms.ValidationError(str(exc)) from exc

        if AwesomeList.objects.filter(
            Q(source_url__iexact=source_url) | Q(repo_full_name__iexact=self.repo_full_name)
        ).exists():
            raise forms.ValidationError("That awesome list is already tracked.")

        if AwesomeListRequest.objects.filter(repo_full_name__iexact=self.repo_full_name).exists():
            raise forms.ValidationError("That awesome-list request has already been submitted.")

        return source_url

    def clean_requester_email(self):
        return self.cleaned_data.get("requester_email", "").strip().lower()

    def clean_note(self):
        return self.cleaned_data.get("note", "").strip()

    def save(self, commit=True):
        request = super().save(commit=False)
        request.repo_full_name = self.repo_full_name
        if commit:
            request.save()
        return request
