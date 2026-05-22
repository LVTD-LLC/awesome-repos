from django import forms
from django.utils.text import slugify

from apps.repos.models import AwesomeList
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
