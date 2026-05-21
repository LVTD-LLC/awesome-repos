<p align="center">
  <img src="#" width="230" alt="Awesome Repos Logo">
</p>

<!--  -->
<div align="center">
  <b>Awesome Repos</b>
  <b>Search and monitor GitHub repositories listed across awesome lists.</b>
</div>

***

## Overview

- Add info about your project here

### Authentication

This project uses `django-allauth` for email/password auth, social auth, and passkeys. Passkey signup is enabled by default and uses mandatory email-code verification before the browser creates the passkey.

### Theme and design system

This template includes a dark/light mode toggle in the navbar. The preference is stored in `localStorage` and applied early to avoid a flash of incorrect theme.

This project also includes a root-level `DESIGN.md` file based on the public Google Labs Code [`DESIGN.md`](https://github.com/google-labs-code/design.md) alpha format. It gives humans and AI coding agents a shared, tool-neutral design source of truth: colors, typography, spacing, radii, component guidance, and practical do/don't rules.

Treat the generated `DESIGN.md` as a generic SaaS baseline. Update it when your brand or UI direction changes, then keep templates/components aligned with it. You can validate it with:

```bash
npx @google/design.md lint DESIGN.md
```

### Frontend

The frontend is intentionally Django-native:

- Django templates are the source of UI truth.
- Tailwind CSS builds from `frontend/src/styles/index.css` to `frontend/static/css/app.css`.
- HTMX handles server-rendered partial updates.
- Alpine.js handles local browser state such as dropdowns, modals, and toggles.
- Small shared browser modules live in `frontend/src/js/` and are copied to `frontend/static/js/` without bundling.

Use `npm run build` before production `collectstatic`, and use `npm run watch` while editing Tailwind or browser modules locally.

When adding interactivity, use HTMX if the server should return fresh HTML. Use Alpine.js when the state is local to the browser. Keep normal Django forms and server validation as the source of truth.

### AI-assisted development

The generated project keeps AI-agent guidance tool-neutral:

- `AGENTS.md` is the canonical repo guidance for AI coding agents and ships
  regardless of optional product feature flags.
Do not add agent-vendor-specific instruction files unless your team explicitly
standardizes on one tool. Keep durable project workflow, test, security, and
architecture rules in `AGENTS.md`.

### Project structure: `/apps`

This project keeps Django apps inside the `/apps` directory. This is both for human clarity and to help AI/code assistants put code in the right place.

- `apps/core`: main app functionality (shared domain logic, base models, services, etc.)
- `apps/docs`: user-facing documentation
- `apps/api`: all API needs (Django Ninja routers, schemas, API-specific logic)

- `apps/pages`: landing/marketing pages (pricing, TOS, privacy policy, etc.)
- `apps/blog`: user-facing blog


### Agent API endpoint

All generated projects include `GET /api/user`, which returns safe account/profile details for the authenticated API key. This is intentionally small but useful as the first "agent can authenticate and know who it is acting for" endpoint.





***

## TOC

- [Overview](#overview)
- [Authentication](#authentication)
- [TOC](#toc)
- [Runtime Versions](#runtime-versions)
- [Theme and design system](#theme-and-design-system)
- [Frontend](#frontend)
- [Deployment](#deployment)
  - [Render](#render)
  - [Fly.io](#flyio)
  - [Docker Compose](#docker-compose)
  - [Pure Python / Django deployment](#pure-python--django-deployment)
  - [Custom Deployment on Caprover](#custom-deployment-on-caprover)
- [Local Development](#local-development)
- [Stripe Setup](#stripe-setup)
  - [Configure Stripe](#configure-stripe)
  - [Test Webhooks Locally](#test-webhooks-locally)

***

## Runtime Versions

- Python 3.14.5
- Django 6.0.5 or newer
- Node.js 24.15.0 LTS
- PostgreSQL 18
- Redis 8.6.3

***

## Deployment

### Render

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/LVTD-LLC/awesome-repos)

**Note:** This should work out of the box with Render's free tier if you provide the AI API keys. Here's what you need to know about the limitations:

- **Worker Service Limitation**: The worker service is not a dedicated worker type (those are only available on paid plans). For the free tier, I had to use a web service through a small hack, but it works fine for most use cases.

- **Memory Constraints**: The free web service has a 512 MB RAM limit, which can cause issues with **automated background tasks only**. When you add a project, it runs a suite of background tasks to analyze your website, generate articles, keywords, and other content. These automated processes can hit memory limits and potentially cause failures.

- **Manual Tasks Work Fine**: However, if you perform tasks manually (like generating a single article), these typically use the web service instead of the worker and should work reliably since it's one request at a time.

- **Upgrade Recommendation**: If you do upgrade to a paid plan, use the actual worker service instead of the web service workaround for better automated task reliability.

**Reality Check**: The website functionality should be usable on the free tier - you'll only pay for API costs. Manual operations work fine, but automated background tasks (especially when adding multiple projects) may occasionally fail due to memory constraints. It's not super comfortable for heavy automated use, but perfectly functional for manual content generation.

If you know of any other services like Render that allow deployment via a button and provide free Redis, Postgres, and web services, please let me know in the [Issues](https://github.com/LVTD-LLC/awesome-repos/issues) section. I can try to create deployments for those. Bear in mind that free services are usually not large enough to run this application reliably.

### Fly.io

This repo includes `fly.toml` for Fly.io deployments. It uses the existing `deployment/Dockerfile`, defines separate `web` and `worker` process groups, runs migrations as a Fly release command, and exposes only the web process.

Quick path:

```bash
fly auth login
fly apps create awesome-repos
fly secrets set SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(50))')"
fly deploy
```

Before deploying, configure Postgres and Redis:

- Set `DATABASE_URL` with Fly.io Managed Postgres, `fly postgres attach`, or another hosted Postgres provider.
- Set `REDIS_URL` from Fly's Upstash Redis integration or another Redis provider.
- Update `app`, `primary_region`, and `SITE_URL` in `fly.toml` if you choose a different Fly app name or region.

### Docker Compose

This should also be pretty streamlined. On your server you can create a folder in which you will have 2 files:

1. `.env`

Copy the contents of `.env.example` into `.env` and update all the necessary values.

If you are pulling the image built by GitHub Actions, set `APP_IMAGE` to `ghcr.io/<owner>/<repository>:latest`. The backend and workers services use that same image; `docker-compose-prod.yml` sets `APP_PROCESS_TYPE=server` for the backend and `APP_PROCESS_TYPE=worker` for workers.

2. `docker-compose-prod.yml`

Copy the contents of `docker-compose-prod.yml` into `docker-compose-prod.yml` and run the suggested command from the top of the `docker-compose-prod.yml` file.

How you are going to expose the backend container is up to you. I usually do it via Nginx Reverse Proxy with `http://awesome_repos-backend-1:80` UPSTREAM_HTTP_ADDRESS.


### Pure Python / Django deployment

Not recommended due to not being too safe for production and not being tested by me.

If you are not into Docker or Render and just wanto to run this via regular commands you will need to have 5 processes running:
- `uv sync --locked --no-dev --no-install-project && npm install && npm run build && uv run --no-sync python manage.py collectstatic --noinput && uv run --no-sync python manage.py migrate && uv run --no-sync gunicorn ${PROJECT_NAME}.wsgi:application --bind 0.0.0.0:80 --workers 3 --threads 2`

- `uv run --no-sync python manage.py qcluster`
- `postgres`
- `redis`

You'd still need to make sure .env has correct values.

### Custom Deployment on Caprover

1. Create 4 apps on CapRover.
  - `awesome_repos`
  - `awesome_repos-workers`
  - `awesome_repos-postgres`
  - `awesome_repos-redis`

2. Create a new CapRover app token for:
   - `awesome_repos`
   - `awesome_repos-workers`

3. Add Environment Variables to those same apps from `.env`.

   Keep `APP_PROCESS_TYPE=server` on the `awesome_repos` app. Set `APP_PROCESS_TYPE=worker` on the `awesome_repos-workers` app because both CapRover apps now run the same Docker image.

4. Create a GitHub Actions repository variable:
   - `WORKERS_APP_PROCESS_TYPE=worker`

   This makes the deploy workflow fail before deploying workers unless the workers process type has been explicitly acknowledged.

5. Create a new GitHub Actions secret with the following:
   - `CAPROVER_SERVER`
   - `APP_TOKEN`
   - `WORKERS_APP_TOKEN`

6. Then just push main branch.

7. Github Workflow in this repo builds one GHCR image and deploys it to both CapRover apps.

## Local Development

`uv.lock` should be generated automatically when Cookiecutter creates this project. Commit it with the rest of the generated app. If it is missing, run `uv lock` once before deploying.

1. Update the name of the `.env.example` to `.env` and update relevant variables.
2. Run `uv sync`
3. Run `npm install && npm run build`
4. Run `uv run python manage.py makemigrations`
   - Important: run this **without specifying app names** so Django detects changes across **all apps**.
   - Do this before feature work and before first local run.
5. Run `make serve`
   - The frontend dev container runs `npm run watch` so Tailwind CSS and browser modules rebuild while you edit templates, styles, and JavaScript.
6. Run `make restart-worker` just in case, it sometimes has troubles connecting to REDIS on first deployment.

Redis backs both Django's default cache and the background worker queue. Configure it with the `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`, and `REDIS_DB` values in `.env`, or set `REDIS_URL` to override them with a full connection URL.

Postgres can be configured with `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, and `POSTGRES_PASSWORD`, or with `DATABASE_URL` to support hosted providers such as Fly.io Postgres.

### CI (optional)

If you generated the project with `use_ci = y`, it includes a GitHub Actions workflow at `.github/workflows/ci.yml` that runs on pull requests.

It boots Postgres + Redis, runs `python manage.py makemigrations --check --dry-run`, then `python manage.py check`, and then runs `pytest`.

If you don’t want CI, set `use_ci = n` during Cookiecutter generation and the workflow will be removed.



