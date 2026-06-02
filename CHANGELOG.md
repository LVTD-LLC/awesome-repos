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
- Awesome: added one-time Stripe checkout onboarding for $1,000 sponsor ads, including a TrustMRR-style modal, post-payment notification email, and paid ad-details submission flow.
- Awesome: added optional Chatwoot live-chat widget support configured with `CHATWOOT_BASE_URL` and `CHATWOOT_WEBSITE_TOKEN`.
- Awesome: added local logo assets and wired the navbar, favicon, touch icon, README, and base social metadata to the new branding.
- Awesome: ingest GitHub awesome-list READMEs, index the linked repositories, and expose searchable repository/list detail pages with stars, freshness, archive-state, and cross-list counts.
- Awesome: added an admin-panel flow to create new awesome-list sources and queue their initial scan.
- Awesome admin panel now shows GitHub API rate-limit status for the configured scanner token.
- Awesome admin panel now lets superusers retry scans for existing awesome-list repos.
- Awesome: added a daily scheduled task that queues a capped number of newly discovered repositories from awesome-list READMEs.
- Awesome: added pgvector-backed repository embeddings from GitHub descriptions and READMEs via OpenRouter/PydanticAI.
- Awesome search filters now expose semantic relevance mode for repository queries.
- Awesome: record repository metadata snapshots on every GitHub refresh and show tracked star growth in repository search/detail pages.
- Awesome: record default-branch commit counts during repository refreshes and show commit growth in repository history.
- Awesome: added a daily budgeted repository refresh that walks the oldest-synced repositories first and uses the same full-source sync path as manual repository rescans.
- Awesome: store each ingested repository README alongside the GitHub API metadata.
- Awesome: detect AI development config files during repository sync and add an AI dev signals filter to repository search.
- Awesome: generate repository discovery tags from descriptions and READMEs, and add filters for generated tags and GitHub topics.
- Awesome: added a daily generated-tag backfill task so existing repository rows get tagged outside GitHub metadata refreshes.
- Awesome: added an awesome-list directory and detail pages with stored list activity metrics including stars, commits, README repository counts, forks, issues, and scan freshness.
- Awesome: record awesome-list GitHub metadata snapshots and show list-level likes/stars and commit charts on awesome-list detail pages.
- Awesome: added D3 charts to repository detail pages for historical stars and commit counts.
- Awesome: added search, filters, and sorting to awesome-list detail repository tables.
- Awesome: added superuser-only catalog maintenance controls on awesome-list and repository detail pages, plus a missing-repository discovery action for awesome lists.
- Awesome search now has desktop side sponsor placements for future ads.
- Awesome: show semantically similar repositories on repository detail pages when pgvector embeddings are available.
- Awesome: added API endpoints for authenticated repository search, repository detail, awesome-list search/detail, and list-scoped repository search.
- Awesome: added an authenticated Streamable HTTP MCP endpoint at `/mcp` for AI agents to search repositories and awesome lists.
- Awesome: added a public awesome-list request form with an admin-reviewable request queue.
- Awesome: repository topic badges now link to the matching topic-filtered search results.
- Awesome: expanded desktop side sponsor rails to ten placements, including one open "Get sponsored" slot.
- Awesome: store first-commit dates for awesome lists and repositories, show them in search/detail pages, and add age filters.
- Awesome: added a management command to backfill first-commit dates for existing awesome-list and repository rows.
- Awesome: detect awesome-list repositories during repository sync and hide them from normal repository browse/search surfaces.
- Awesome: added opt-in GitHub starred repository imports with a personal starred-repo search surface and daily user-token refreshes.
- Awesome: added a liked repositories page for authenticated users.
- Awesome: detect repository dependency manifests during sync, infer package managers and stacks such as Django, Next.js, Rails, and Axum, and expose stack/package-manager filters in the UI, API, and MCP search tools.
- Awesome: store repository website links from GitHub metadata or description URLs and show them on repository pages.
- Awesome: added experimental superuser-only repository newsletters with tracked commits, generated issues, RSS feeds, and email delivery.
- Awesome: added repository search filters for detected frameworks, unmaintained repositories, tracked commit velocity, tracked star growth, and sort direction.
- Awesome: added a recently-starred sort for personal GitHub starred repository search.

### Changed
- Awesome: unified repository search filters across global search, awesome-list repository search, and personal starred-repository search.
- Awesome: added Settings to the account navbar, simplified Settings around GitHub imports and future repository update preferences, and moved awesome-list requests into the Lists page flow.
- Awesome: compacted repository-detail AI development signals into summary badges and bounded config-path lists.
- Awesome: GitHub signups now land on Settings so starred-repository imports stay off by default until the user clicks the import CTA.
- Awesome: updated default contact email and production domain references to rasul@lvtd.dev and awesome.lvtd.dev.
- Awesome: renamed product-facing copy and brand assets to the shorter product name.
- Awesome: awesome-list detail pages now show list-level GitHub stars and commits instead of aggregate repository growth charts.
- Awesome: replaced placeholder side-rail sponsor slots with equal-height ads for LVTD projects and attribution-tagged outbound links.
- Awesome: moved desktop side sponsor rails closer to the viewport edges while keeping page content centered.
- Awesome: standardized page width around global side ad rails with five sponsor slots on each side.
- Awesome: repository search is now the root landing page, with `/repos/` permanently redirecting to `/` and a prominent link to the `/lists/` awesome-list directory.
- Awesome: admin navbar now links directly to Repos and Lists instead of Dashboard and Settings.
- Awesome: moved repository search filters into a compact vertical modal opened from a single filter button.
- Awesome: replaced separate public/app navigation with a shared search-first navbar that exposes repos, lists, starred repos, liked repos, and list requests.
- Awesome admin-panel add flow now only asks for the GitHub URL; list names and slugs are derived automatically from the source repo.
- Awesome-list scans now log start/finish/failure details and surface empty scans or sync failures in the admin panel.
- Awesome: repository detail history now relies on D3 charts instead of duplicating growth cards and a snapshot table.
- Awesome: GitHub star counts now render with thousands separators, and repository search results no longer show tracked star-growth deltas.
- Awesome: moved the MCP endpoint into its own Django app and rebuilt it on FastMCP while keeping API and MCP search payloads on shared service functions.
- Awesome: repository generated-tag prompts now include known language, GitHub topics, and AI-development signals.

### Fixed
- Awesome: keep personal Starred and Liked nav links hidden from anonymous visitors.
- Awesome: keep explicitly liked repositories visible in the personal liked page even when hidden from public catalog search.
- Awesome: trapped keyboard focus inside the list-request and delete-account modals.
- Awesome: return users to Settings after connecting GitHub and style the allauth connected-accounts fallback page.
- Awesome: fixed invalid nested links on awesome-list repository cards that created empty clickable containers.
- Awesome: loosened desktop side sponsor rail spacing so ad cards no longer crowd or overlap their copy.
- Awesome: repair repository migration graph ordering so production can migrate past the AI-development and activity merge branches.
- Awesome admin panel now bounds the Recent awesome lists card height so long list histories do not stretch the dashboard row.
- Awesome: include `django.contrib.postgres` so pgvector HNSW indexes pass Django production checks.
- Awesome: removed manifest-dependent logo static references so production template rendering works after `collectstatic`.
- Awesome: run generated-tag sync when a repository has no stored generated tags, including metadata-only refreshes.
- Awesome: removed side sponsor rail reservations from awesome-list detail pages to keep the repository list layout balanced.

### Changed
- Awesome landing pages no longer render a public navbar.
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
- Awesome: removed the feedback collection widget, API endpoint, admin feedback stats, and stored feedback model.
- Stimulus, Webpack, `python-webpack-boilerplate`, manifest loading, and generated Webpack configuration.

### Fixed
- Local Docker Compose now waits for the frontend watcher to finish its first asset build, and `npm run watch` now keeps browser modules in sync while editing JavaScript.
