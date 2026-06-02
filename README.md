<p align="center">
  <img src="frontend/static/brand/awesome-repos-logo.svg" width="360" alt="Awesome logo">
</p>

<div align="center">
  <strong>Search and monitor GitHub repositories listed across awesome lists.</strong>
</div>

## What is Awesome?

Awesome is a free, open-source discovery tool for GitHub projects that appear in curated
awesome lists.

Awesome ingests GitHub awesome-list READMEs, extracts repository links, enriches those
repositories with GitHub metadata, and gives people one searchable place to find projects
that are maintained, relevant, and repeatedly recommended by curators.

## Why this exists

Awesome lists are valuable, but they are scattered across GitHub and hard to compare.
The goal of this project is to make that ecosystem easier to explore:

- Find repositories across many awesome lists from one search surface.
- Compare projects by stars, age, freshness, archive status, language, topics, tags, and
  list mentions.
- Spot strong cross-list recommendations instead of relying on a single README.
- Keep the tool free for users while keeping the codebase open for inspection and
  contribution.

## What the app does today

- Indexes GitHub awesome lists and the repositories linked from their READMEs.
- Stores repository metadata, README content, topics, generated tags, and historical
  snapshots.
- Provides repository search, awesome-list search, detail pages, filters, and sorting.
- Shows repository history, similar repositories, and list membership where data is
  available.
- Lets users request new awesome lists to add to the catalog.
- Exposes authenticated API and MCP surfaces for integrations and AI-agent workflows.

## Project status

Awesome is actively evolving. The product is intended to be used as a hosted free tool,
not as a self-hosted application, so this README focuses on the project, contribution
workflow, and local development basics instead of production deployment recipes.

## Repository layout

- `apps/repos/` - awesome-list ingestion, repository metadata, search services, tasks,
  and repository tests.
- `apps/api/` - authenticated API schemas, routers, and shared search payloads.
- `apps/mcp_server/` - MCP transport and tools for agent access.
- `apps/core/` - shared application views, auth-adjacent flows, profiles, forms, and
  common tests.
- `apps/pages/` - static pages and simple marketing pages.
- `frontend/templates/` - Django templates.
- `frontend/src/styles/` - Tailwind CSS source.
- `frontend/src/js/` - small browser modules copied into Django static assets.
- `DESIGN.md` - design-system guidance for humans and coding agents.
- `AGENTS.md` - contributor and coding-agent workflow guidance.

## Local development

Prerequisites are managed through the project tooling. For normal local work:

```bash
cp .env.example .env
uv sync
npm install
npm run build
uv run python manage.py migrate
make serve
```

Useful checks:

```bash
uv run python manage.py check
uv run pytest -q
npm run lint
```

If you change models, create and inspect migrations before opening a pull request:

```bash
uv run python manage.py makemigrations
uv run python manage.py makemigrations --check --dry-run
```

### API and MCP

Authenticated API keys can read account and catalog data from the API. Repository
search accepts filters such as `q`, `language`, `list`, `topic`,
`generated_tag`, `framework`, `stack`, `package_manager`, `min_stars`,
`updated_days`, `unmaintained_days`, `min_velocity_percent`,
`min_liability_percent`, `min_age_years`, `archived`, `ai_development`,
`sort`, and `sort_direction`.

Repository detail responses include dependency-file stack detection, list
membership, growth history, README content, and similar repositories.

Awesome also exposes a Streamable HTTP MCP endpoint at `/mcp` so AI agents can
use the same search surface as the API. Use an account API key as either
`Authorization: Bearer <api-key>` or `X-API-Key: <api-key>`.

### Catalog operations

Repository stack detection runs during normal repository refreshes by scanning
GitHub tree metadata for dependency manifests, fetching bounded manifest
contents, and storing parsed package managers, dependency ecosystems, and
inferred stack signals. Operators can backfill existing rows with:

```bash
uv run python manage.py detect_repository_stacks --limit 100
uv run python manage.py detect_repository_stacks --all --dry-run
```

## Contributing

Contributions are welcome when they improve the public tool, the catalog quality, or the
maintainability of the codebase.

Good places to help:

- Improve search, filters, and repository ranking.
- Add better catalog maintenance workflows.
- Improve metadata extraction from awesome-list READMEs.
- Polish UI states, accessibility, and responsive behavior.
- Add focused tests around ingestion, search, and user-facing flows.
- Report broken metadata, stale lists, or useful awesome lists that should be tracked.

Before changing code, read `AGENTS.md`, `DESIGN.md`, and the files around the area you are
touching. Keep changes small, tested, and aligned with the existing Django app structure.

## License

This project is intended to remain open source. License terms should be added before
publishing reuse guidance.
