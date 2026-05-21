# AGENTS.md - Awesome Repos

This is a generated Django SaaS app. Keep changes small, tested, and aligned
with the app structure that Cookiecutter created.

## Agent Contract

- Treat this file as the canonical guidance for coding agents in this project.
- Keep guidance tool-neutral. Do not add IDE-specific, vendor-specific, or
  single-agent instruction files.
- Add nested `AGENTS.md` files only when a subdirectory needs scoped guidance.
- Keep personal preferences and machine-local paths out of committed
  instructions.
- Store secrets in environment variables or `.env`; never print, log, hard-code,
  or commit API keys.

## Project Map

- `apps/core/` - shared domain logic, auth-adjacent flows, profiles, forms,
  utilities, background tasks, and common tests.
- `apps/pages/` - landing, pricing, legal, and other static or marketing pages.
- `apps/api/` - Django Ninja API schemas, auth, services, and routers.
- `apps/core/agents/` - PydanticAI model helpers and agent code.
- `awesome_repos/settings.py` - environment-driven Django
  settings.
- `awesome_repos/urls.py` - top-level URL routing.
- `frontend/templates/` - Django templates.
- `frontend/src/js/` - small browser modules copied to `frontend/static/js/`.
- `frontend/src/styles/` - Tailwind CSS and global styles.
- `frontend/static/` - Django-served static asset output.
- `DESIGN.md` - design-system source of truth for humans and AI tools.

## Workflow

1. Read `README.md`, `DESIGN.md`, and the files around the requested change.
2. Create a branch before implementation when working in git.
3. Put code in the smallest appropriate app or frontend module.
4. Add or update tests for feature work, bug fixes, and risky refactors.
5. Run targeted checks first, then broader checks before finishing.
6. Update `CHANGELOG.md` for user-visible behavior changes.

## Commands

Local Docker-backed development:

```bash
make serve
make manage check
make test
```

Targeted tests:

```bash
make test apps/core/tests/test_example.py
make test apps/core/tests/test_example.py::test_specific_case
make test -- -k keyword -q
```

Host-level checks used by CI:

```bash
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py check
uv run pytest -q
```

Frontend:

```bash
npm install
npm run build
npm run lint
```

## Implementation Rules

- Use Django conventions and the existing app boundaries before creating new
  abstractions.
- Keep business logic out of templates; use views, forms, services, model
  methods, or template tags as appropriate.
- Change models first, then generate migrations with `make makemigrations`.
  Inspect generated migrations before committing them.
- Do not hand-edit historical migrations unless explicitly required.
- Keep auth flows compatible with `django-allauth`, including email, passkey,
  MFA, signup gating, and password reset flows.
- Keep light and dark mode readable when changing templates.
- Use HTMX for server-rendered partial updates and Alpine.js for local browser
  state. Keep plain browser modules in `frontend/src/js/` for shared DOM
  behavior.
- Keep styles aligned with `DESIGN.md` and Tailwind conventions.
- For PydanticAI work, prefer typed dependencies, typed outputs, stable
  constructor `instructions`, and small dynamic `@agent.instructions`
  functions.
## Agent Guidance

- This file ships regardless of optional product features. Do not tie
  development-agent guidance to runtime feature flags.
- Keep this file concise enough for agents to read before every task.
- Prefer exact commands and file paths over generic "follow best practices"
  instructions.
- Update this file when project structure, test commands, security constraints,
  or major workflows change.
