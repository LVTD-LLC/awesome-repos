# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project tries to adhere to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Types of changes

**Added** for new features.
**Changed** for changes in existing functionality.
**Deprecated** for soon-to-be removed features.
**Removed** for now removed features.
**Fixed** for any bug fixes.
**Security** in case of vulnerabilities.


## [Unreleased]
### Added
- Awesome Repos: ingest GitHub awesome-list READMEs, index the linked repositories, and expose searchable repository/list detail pages with stars, freshness, archive-state, and cross-list counts.
- Awesome Repos: added an admin-panel flow to create new awesome-list sources and queue their initial scan.
- Awesome Repos admin panel now shows GitHub API rate-limit status for the configured scanner token.
- Awesome Repos admin panel now lets superusers retry scans for existing awesome-list repos.
- Awesome Repos: added a daily scheduled task that queues per-list missing repository discovery and only ingests newly discovered repositories.
- Awesome Repos: added pgvector-backed repository embeddings from GitHub descriptions and READMEs via OpenRouter/PydanticAI.
- Awesome Repos: record repository metadata snapshots on every GitHub refresh and show tracked star growth in repository search/detail pages.
- Awesome Repos: added a monthly repository metadata refresh schedule that fans out one background task per saved repository.
- Awesome Repos: store each ingested repository README alongside the GitHub API metadata.
- Awesome Repos: record default-branch commit counts during repository refreshes and show commit growth in repository history.
- Awesome Repos: generate repository discovery tags from descriptions and READMEs, and add filters for generated tags and GitHub topics.

### Changed
- Awesome Repos admin-panel add flow now only asks for the GitHub URL; list names and slugs are derived automatically from the source repo.
- Awesome-list scans now log start/finish/failure details and surface empty scans or sync failures in the admin panel.

### Fixed
- Awesome Repos admin panel now bounds the Recent awesome lists card height so long list histories do not stretch the dashboard row.
- Awesome Repos: include `django.contrib.postgres` so pgvector HNSW indexes pass Django production checks.
- Awesome Repos: removed manifest-dependent logo static references so production template rendering works after `collectstatic`.

### Changed
- Awesome Repos landing pages now use a minimal header and no longer show sign-in/sign-up navbar buttons.
- Frontend assets now use Tailwind CLI plus Django staticfiles instead of a JavaScript bundler.
- AI-assisted development guidance now uses tool-neutral `AGENTS.md` files
  instead of agent-vendor-specific instruction files.
- Deployments now use one shared Docker image, one CapRover deploy workflow, and explicit `APP_PROCESS_TYPE` guards to choose server vs. worker at runtime.
- Align template runtimes on Python 3.14.5, Django 6.0.5, Node.js 24.15.0 LTS, PostgreSQL 18, and Redis 8.6.3.
- Sentry setup now includes release metadata, configurable tracing/profiling/log settings, logging breadcrumbs/events, and the `before_send` hook by default.

### Added
- Fly.io deployment support with `fly.toml`, web and worker process groups, migration release commands, and `DATABASE_URL` support.
- HTMX, django-htmx middleware, Alpine.js, and frontend rules for Django-native interactivity.
- `ALLOW_SIGNUPS` environment flag (default `True`) to pause new email/social registrations while keeping existing user logins available.
- Superuser-only admin blog API for creating, listing, reading, updating, patching, deleting, reviewing, and publishing blog posts when the blog app is generated.

### Removed
- Stimulus, Webpack, `python-webpack-boilerplate`, manifest loading, and generated Webpack configuration.

### Fixed
- Local Docker Compose now waits for the frontend watcher to finish its first asset build, and `npm run watch` now keeps browser modules in sync while editing JavaScript.
