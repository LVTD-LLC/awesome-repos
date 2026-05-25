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
- Awesome Repos: added optional Chatwoot live-chat widget support configured with `CHATWOOT_BASE_URL` and `CHATWOOT_WEBSITE_TOKEN`.
- Awesome Repos: added local logo assets and wired the navbar, favicon, touch icon, README, and base social metadata to the new branding.
- Awesome Repos: ingest GitHub awesome-list READMEs, index the linked repositories, and expose searchable repository/list detail pages with stars, freshness, archive-state, and cross-list counts.
- Awesome Repos: added an admin-panel flow to create new awesome-list sources and queue their initial scan.
- Awesome Repos admin panel now shows GitHub API rate-limit status for the configured scanner token.
- Awesome Repos admin panel now lets superusers retry scans for existing awesome-list repos.
- Awesome Repos: added a daily scheduled task that queues a capped number of newly discovered repositories from awesome-list READMEs.
- Awesome Repos: added pgvector-backed repository embeddings from GitHub descriptions and READMEs via OpenRouter/PydanticAI.
- Awesome Repos search filters now expose semantic relevance mode for repository queries.
- Awesome Repos: record repository metadata snapshots on every GitHub refresh and show tracked star growth in repository search/detail pages.
- Awesome Repos: record default-branch commit counts during repository refreshes and show commit growth in repository history.
- Awesome Repos: added a daily budgeted repository refresh that walks the oldest-synced repositories first and uses the same full-source sync path as manual repository rescans.
- Awesome Repos: store each ingested repository README alongside the GitHub API metadata.
- Awesome Repos: detect AI development config files during repository sync and add an AI dev signals filter to repository search.
- Awesome Repos: generate repository discovery tags from descriptions and READMEs, and add filters for generated tags and GitHub topics.
- Awesome Repos: added an awesome-list directory and detail pages with stored list activity metrics including stars, commits, README repository counts, forks, issues, and scan freshness.
- Awesome Repos: added D3 charts to repository detail pages for historical stars and commit counts.
- Awesome Repos: added search, filters, and sorting to awesome-list detail repository tables.
- Awesome Repos: added superuser-only catalog maintenance controls on awesome-list and repository detail pages, plus a missing-repository discovery action for awesome lists.
- Awesome Repos search now has desktop side sponsor placements for future ads.
- Awesome Repos: show semantically similar repositories on repository detail pages when pgvector embeddings are available.
- Awesome Repos: added API endpoints for authenticated repository search, repository detail, awesome-list search/detail, and list-scoped repository search.
- Awesome Repos: added an authenticated Streamable HTTP MCP endpoint at `/mcp` for AI agents to search repositories and awesome lists.
- Awesome Repos: added a public awesome-list request form with an admin-reviewable request queue.
- Awesome Repos: repository topic badges now link to the matching topic-filtered search results.
- Awesome Repos: added a tenth desktop side sponsor placement with one open "Get sponsored" slot.

### Changed
- Awesome Repos: replaced placeholder side-rail sponsor slots with equal-height ads for LVTD projects and attribution-tagged outbound links.
- Awesome Repos: standardized page width around global side ad rails with five sponsor slots on each side.
- Awesome Repos: repository search is now the root landing page, with `/repos/` permanently redirecting to `/` and a prominent link to the `/lists/` awesome-list directory.
- Awesome Repos: moved repository search filters into a compact vertical modal opened from a single filter button.
- Awesome Repos admin-panel add flow now only asks for the GitHub URL; list names and slugs are derived automatically from the source repo.
- Awesome-list scans now log start/finish/failure details and surface empty scans or sync failures in the admin panel.
- Awesome Repos: repository detail history now relies on D3 charts instead of duplicating growth cards and a snapshot table.
- Awesome Repos: GitHub star counts now render with thousands separators, and repository search results no longer show tracked star-growth deltas.
- Awesome Repos: moved the MCP endpoint into its own Django app and rebuilt it on FastMCP while keeping API and MCP search payloads on shared service functions.

### Fixed
- Awesome Repos: repair repository migration graph ordering so production can migrate past the AI-development and activity merge branches.
- Awesome Repos admin panel now bounds the Recent awesome lists card height so long list histories do not stretch the dashboard row.
- Awesome Repos: include `django.contrib.postgres` so pgvector HNSW indexes pass Django production checks.
- Awesome Repos: removed manifest-dependent logo static references so production template rendering works after `collectstatic`.
- Awesome Repos: run generated-tag sync when a repository has no stored generated tags, including metadata-only refreshes.

### Changed
- Awesome Repos landing pages no longer render a public navbar.
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
