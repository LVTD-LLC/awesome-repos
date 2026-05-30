# Graph Report - .  (2026-05-30)

## Corpus Check
- 185 files · ~62,438 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1083 nodes · 2284 edges · 100 communities (72 shown, 28 thin omitted)
- Extraction: 85% EXTRACTED · 15% INFERRED · 0% AMBIGUOUS · INFERRED: 352 edges (avg confidence: 0.55)
- Token cost: 282,066 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Test & Model Scaffolding|Test & Model Scaffolding]]
- [[_COMMUNITY_API Schema (Ninja InOut)|API Schema (Ninja In/Out)]]
- [[_COMMUNITY_Admin Panel Views & URLs|Admin Panel Views & URLs]]
- [[_COMMUNITY_Awesome List Detail Tests|Awesome List Detail Tests]]
- [[_COMMUNITY_API Authentication|API Authentication]]
- [[_COMMUNITY_AI Repo Tagging Agent|AI Repo Tagging Agent]]
- [[_COMMUNITY_ASGI & URL Routing|ASGI & URL Routing]]
- [[_COMMUNITY_Repository Sync Tasks|Repository Sync Tasks]]
- [[_COMMUNITY_Email & Ad Layout Tests|Email & Ad Layout Tests]]
- [[_COMMUNITY_Frontend Dependencies|Frontend Dependencies]]
- [[_COMMUNITY_Frontend JS Utilities|Frontend JS Utilities]]
- [[_COMMUNITY_Repository Embeddings|Repository Embeddings]]
- [[_COMMUNITY_Repo & List Templates|Repo & List Templates]]
- [[_COMMUNITY_GitHub Fetch Services|GitHub Fetch Services]]
- [[_COMMUNITY_Auth Page Templates|Auth Page Templates]]
- [[_COMMUNITY_Deployment & Design Docs|Deployment & Design Docs]]
- [[_COMMUNITY_FastMCP Tools|FastMCP Tools]]
- [[_COMMUNITY_Repository Upsert Tests|Repository Upsert Tests]]
- [[_COMMUNITY_Search Serializers|Search Serializers]]
- [[_COMMUNITY_Email Delivery Utils|Email Delivery Utils]]
- [[_COMMUNITY_Awesome List Request Forms|Awesome List Request Forms]]
- [[_COMMUNITY_Allauth Adapters|Allauth Adapters]]
- [[_COMMUNITY_API Key Hashing|API Key Hashing]]
- [[_COMMUNITY_Logging & Settings|Logging & Settings]]
- [[_COMMUNITY_Account Forms|Account Forms]]
- [[_COMMUNITY_views.py|views.py]]
- [[_COMMUNITY_DetailView|DetailView]]
- [[_COMMUNITY_datetime|datetime]]
- [[_COMMUNITY_repository-history-charts.js|repository-history-charts.js]]
- [[_COMMUNITY_ApiConfig|ApiConfig]]
- [[_COMMUNITY_admin.py|admin.py]]
- [[_COMMUNITY_sync_awesome_repos.py|sync_awesome_repos.py]]
- [[_COMMUNITY__get_api_key_from_headers()|_get_api_key_from_headers()]]
- [[_COMMUNITY_context_processors.py|context_processors.py]]
- [[_COMMUNITY_int|int]]
- [[_COMMUNITY_test_signup_gating.py|test_signup_gating.py]]
- [[_COMMUNITY_test_signals.py|test_signals.py]]
- [[_COMMUNITY_admin.py|admin.py]]
- [[_COMMUNITY_copyAppJs()|copyAppJs()]]
- [[_COMMUNITY_ValueError|ValueError]]
- [[_COMMUNITY__apply_list_repository_state_f|_apply_list_repository_state_f]]
- [[_COMMUNITY_sitemaps.py|sitemaps.py]]
- [[_COMMUNITY_tasks.py|tasks.py]]
- [[_COMMUNITY_0008_schedule_daily_budgeted_r|0008_schedule_daily_budgeted_r]]
- [[_COMMUNITY_0014_repository_awesome_list_d|0014_repository_awesome_list_d]]
- [[_COMMUNITY_0003_schedule_monthly_reposito|0003_schedule_monthly_reposito]]
- [[_COMMUNITY_detect_awesome_list_candidate(|detect_awesome_list_candidate(]]
- [[_COMMUNITY_Apple Touch Icon|Apple Touch Icon]]
- [[_COMMUNITY_DJANGO_SETTINGS_MODULE|DJANGO_SETTINGS_MODULE]]
- [[_COMMUNITY__apply_list_repository_keyword|_apply_list_repository_keyword]]
- [[_COMMUNITY_CustomS3Boto3Storage|CustomS3Boto3Storage]]
- [[_COMMUNITY_Email Confirmation Message|Email Confirmation Message]]
- [[_COMMUNITY_0002_schedule_daily_missing_re|0002_schedule_daily_missing_re]]
- [[_COMMUNITY_conftest.py|conftest.py]]
- [[_COMMUNITY_main()|main()]]
- [[_COMMUNITY_0001_enable_extensions.py|0001_enable_extensions.py]]
- [[_COMMUNITY_Pre-commit config|Pre-commit config]]
- [[_COMMUNITY_0003_alter_emailsent_email_typ|0003_alter_emailsent_email_typ]]
- [[_COMMUNITY_0001_initial.py|0001_initial.py]]
- [[_COMMUNITY_0001_initial.py|0001_initial.py]]
- [[_COMMUNITY_0005_repositoryembedding.py|0005_repositoryembedding.py]]
- [[_COMMUNITY_0007_repository_generated_tags|0007_repository_generated_tags]]
- [[_COMMUNITY_0008_awesomelist_commits_count|0008_awesomelist_commits_count]]
- [[_COMMUNITY_0010_repository_commit_count_a|0010_repository_commit_count_a]]
- [[_COMMUNITY_asgi.py|asgi.py]]
- [[_COMMUNITY_wsgi.py|wsgi.py]]
- [[_COMMUNITY_Messages Toast Component|Messages Toast Component]]
- [[_COMMUNITY_Side Ad Rail Component|Side Ad Rail Component]]
- [[_COMMUNITY_Account Already Exists Email M|Account Already Exists Email M]]
- [[_COMMUNITY_0002_initial.py|0002_initial.py]]
- [[_COMMUNITY_0004_repositorysnapshot.py|0004_repositorysnapshot.py]]
- [[_COMMUNITY_0006_repository_readme_reposit|0006_repository_readme_reposit]]
- [[_COMMUNITY_0008_repository_ai_development|0008_repository_ai_development]]
- [[_COMMUNITY_0009_merge_20260522_awesome_li|0009_merge_20260522_awesome_li]]
- [[_COMMUNITY_0011_merge_ai_development_sign|0011_merge_ai_development_sign]]
- [[_COMMUNITY_0012_awesome_list_request.py|0012_awesome_list_request.py]]
- [[_COMMUNITY_0013_awesomelist_first_commit_|0013_awesomelist_first_commit_]]
- [[_COMMUNITY_copy-vendor-assets.mjs|copy-vendor-assets.mjs]]
- [[_COMMUNITY_Chatwoot Widget Component|Chatwoot Widget Component]]

## God Nodes (most connected - your core abstractions)
1. `Repository` - 84 edges
2. `AwesomeList` - 74 edges
3. `str` - 42 edges
4. `upsert_repository_from_github()` - 38 edges
5. `RepositorySnapshot` - 33 edges
6. `RepositoryEmbedding` - 33 edges
7. `AwesomeListItem` - 29 edges
8. `int` - 25 edges
9. `datetime` - 25 edges
10. `HttpRequest` - 25 edges

## Surprising Connections (you probably didn't know these)
- `Render web service (awesome_repos-web)` --semantically_similar_to--> `CapRover deployment`  [INFERRED] [semantically similar]
  render.yaml → .github/workflows/deploy.yml
- `Local Docker Compose` --semantically_similar_to--> `Production Docker Compose`  [INFERRED] [semantically similar]
  docker-compose-local.yml → docker-compose-prod.yml
- `CustomAccountAdapter` --uses--> `EmailType`  [INFERRED]
  awesome_repos/adapters.py → apps/core/choices.py
- `CustomSocialAccountAdapter` --uses--> `EmailType`  [INFERRED]
  awesome_repos/adapters.py → apps/core/choices.py
- `test_confirmation_mail_failures_do_not_bubble_to_signup()` --calls--> `CustomAccountAdapter`  [EXTRACTED]
  apps/core/tests/test_email_delivery.py → awesome_repos/adapters.py

## Hyperedges (group relationships)
- **Multi-target deployment topology** — render_awesome_repos_web, docker_compose_prod, deploy_caprover, deploy_ghcr_image [INFERRED 0.75]
- **Web + workers + Postgres + Redis runtime stack** — render_awesome_repos_web, render_awesome_repos_workers, render_awesome_repos_db, render_awesome_repos_redis [EXTRACTED 1.00]
- **Shared repository search surface (API + MCP + pgvector)** — readme_api_endpoints, readme_fastmcp_server, changelog_pgvector_embeddings [INFERRED 0.85]
- **Landing-Based Page Templates** — templates_base_landing, repos_search, repos_detail, repos_lists, repos_list_detail [EXTRACTED 1.00]
- **MFA Template Inheritance Chain** — templates_base_app, mfa_base_manage, webauthn_base, recovery_codes_base [EXTRACTED 1.00]
- **WebAuthn Scripts Snippet Consumers** — snippets_scripts, webauthn_signup_form, webauthn_add_form, snippets_login_script [EXTRACTED 1.00]
- **Allauth Account Auth Pages** — account_login, account_signup, account_signup_by_passkey, account_logout, account_email [INFERRED 0.85]
- **Email Verification Flow** — account_email_confirm, account_confirm_email_verification_code, components_confirm_email [INFERRED 0.75]
- **Side Ad Rail System** — components_side_ad_rail, components_side_ad_slot [EXTRACTED 1.00]
- **Email Confirmation Template Set** — email_email_confirmation_message, email_email_confirmation_signup_message, email_account_already_exists_message [INFERRED 0.75]
- **Landing-Based Pages** — pages_privacy_policy, pages_uses, pages_landing_page, pages_terms_of_service [EXTRACTED 1.00]
- **App-Based Pages** — pages_admin_panel, pages_home, pages_user_settings [EXTRACTED 1.00]
- **Awesome Repos Brand Asset System** — brand_awesome_repos_mark, brand_awesome_repos_logo, brand_awesome_repos_social, brand_apple_touch_icon [INFERRED 0.85]

## Communities (100 total, 28 thin omitted)

### Community 0 - "Test & Model Scaffolding"
Cohesion: 0.07
Nodes (45): PlaceholderApiTests, UserInfoApiUnitTests, bool, str, str, bool, int, str (+37 more)

### Community 1 - "API Schema (Ninja In/Out)"
Cohesion: 0.08
Nodes (55): AwesomeListCreateIn, AwesomeListDetailOut, AwesomeListDirectoryTotalsOut, AwesomeListMutationOut, AwesomeListReferenceOut, AwesomeListRepositoryStatsOut, AwesomeListSearchOut, AwesomeListSummaryOut (+47 more)

### Community 2 - "Admin Panel Views & URLs"
Cohesion: 0.05
Nodes (26): str, awesome_repos URL Configuration  The `urlpatterns` list routes URLs to views. Fo, AdminPanelView, build_absolute_public_url(), delete_account(), HomeView, Permanently delete the current user and all related data.      Safety: requires, Build a public URL from SITE_URL and upgrade non-local HTTP origins to HTTPS. (+18 more)

### Community 3 - "Awesome List Detail Tests"
Cohesion: 0.05
Nodes (16): repository_search_queryset(), github_awesome_list_payload(), test_awesome_list_form_derives_name_and_unique_slug_from_url(), test_awesome_list_request_admin_clears_reviewed_at_when_reset_to_pending(), test_detect_ai_development_signals_identifies_common_agent_files(), test_fetch_json_uses_github_token(), test_fetch_repository_readme_data_decodes_github_contents_metadata(), test_fetch_repository_readme_decodes_github_contents_payload() (+8 more)

### Community 4 - "API Authentication"
Cohesion: 0.08
Nodes (29): AccessToken, APIKeyHeaderAuth, BearerAPIKeyAuth, Authentication via Django session, _require_superuser(), SessionAuth, SuperuserAPIKeyHeaderAuth, SuperuserBearerAPIKeyAuth (+21 more)

### Community 5 - "AI Repo Tagging Agent"
Cohesion: 0.12
Nodes (35): Agent, build_model(), PydanticAIModelSpec, str, bool, Exception, Repository, str (+27 more)

### Community 6 - "ASGI & URL Routing"
Cohesion: 0.12
Nodes (21): Any, bool, HttpRequest, int, str, ASGIApp, bytes, Event (+13 more)

### Community 7 - "Repository Sync Tasks"
Cohesion: 0.12
Nodes (32): bool, int, str, add_repository_to_awesome_list(), github_rate_limit_remaining(), add_missing_repository_to_awesome_list_task(), _available_repository_refresh_limit(), _daily_missing_repository_budget_key() (+24 more)

### Community 8 - "Email & Ad Layout Tests"
Cohesion: 0.07
Nodes (8): assert_standard_ad_layout(), mark_password_reauthenticated(), test_app_pages_use_standard_ad_layout(), test_public_pages_use_standard_ad_layout(), test_recovery_codes_generate_page_uses_app_styling_and_creates_codes(), test_recovery_codes_page_can_require_save_confirmation(), test_recovery_codes_page_uses_app_styling(), test_webauthn_add_page_loads_styled_form_and_scripts()

### Community 9 - "Frontend Dependencies"
Cohesion: 0.06
Nodes (30): author, bugs, url, dependencies, alpinejs, d3, htmx.org, description (+22 more)

### Community 10 - "Frontend JS Utilities"
Cohesion: 0.11
Nodes (14): copyText(), initCopyButtons(), copyCode(), initDocsEnhancements(), buildMessageElement(), createMessagesContainer(), initMessages(), showMessage() (+6 more)

### Community 11 - "Repository Embeddings"
Cohesion: 0.17
Nodes (24): bool, Repository, str, Command, EmbedInputType, build_repository_embedding_payload(), build_repository_embedding_text(), _embed_text_async() (+16 more)

### Community 12 - "Repo & List Templates"
Cohesion: 0.09
Nodes (27): Allauth WebAuthn JS Integration, Side Ad Rail Component, MFA Base Manage Template, Block: mfa_content, MFA Passkeys Index, Recovery Codes Base Template, Recovery Codes Generate, Recovery Codes Index (+19 more)

### Community 13 - "GitHub Fetch Services"
Cohesion: 0.22
Nodes (24): str, _append_ai_development_signal(), _apply_repository_keyword_search(), _apply_repository_semantic_search(), _capture_github_rate_limit_headers(), detect_ai_development_signals(), fetch_awesome_readme(), _fetch_github_commits_page() (+16 more)

### Community 14 - "Auth Page Templates"
Cohesion: 0.11
Nodes (25): Email Verification Code Page, Email Addresses Page, Confirm Email Address Page, Login Page, Logout Page, Reauthenticate Page, Signup Page, Signup by Passkey Page (+17 more)

### Community 15 - "Deployment & Design Docs"
Cohesion: 0.10
Nodes (25): AGENTS.md agent contract, Awesome-list README ingestion, Awesome Repos Changelog, Streamable HTTP MCP endpoint (/mcp), pgvector repository embeddings, CI GitHub workflow, CapRover deployment, GHCR Docker image (+17 more)

### Community 16 - "FastMCP Tools"
Cohesion: 0.20
Nodes (19): Any, FastMCP, int, str, int, str, Http404, _not_found_error() (+11 more)

### Community 17 - "Repository Upsert Tests"
Cohesion: 0.16
Nodes (20): upsert_repository_from_github(), github_repo_payload(), stub_repository_readme(), test_detect_awesome_list_candidate_uses_readme_links(), test_upsert_repository_from_github_can_refresh_metadata_without_readme(), test_upsert_repository_from_github_marks_awesome_list_candidates(), test_upsert_repository_from_github_preserves_ai_signals_when_tree_fetch_fails(), test_upsert_repository_from_github_preserves_ai_signals_when_tree_is_truncated() (+12 more)

### Community 18 - "Search Serializers"
Cohesion: 0.20
Nodes (17): Repository, awesome_list_search_queryset(), get_awesome_list_detail_payload(), get_awesome_list_repository_options_payload(), serialize_awesome_list_reference(), serialize_awesome_list_repo_stats(), serialize_awesome_list_summary(), serialize_repository_detail() (+9 more)

### Community 19 - "Email Delivery Utils"
Cohesion: 0.22
Nodes (16): Any, bool, Exception, str, bump_email_delivery_metric(), get_email_delivery_provider(), get_email_delivery_retry_backoff_seconds(), is_transient_email_error() (+8 more)

### Community 20 - "Awesome List Request Forms"
Cohesion: 0.13
Nodes (9): ListView, AwesomeListRequestForm, awesome_list_directory_totals(), test_awesome_list_directory_totals_aggregates_in_one_query(), test_awesome_list_request_form_records_normalized_repo_details(), test_awesome_list_request_form_rejects_duplicate_requests_by_repo_name(), test_awesome_list_request_form_rejects_tracked_lists_by_repo_name(), AwesomeListListView (+1 more)

### Community 21 - "Allauth Adapters"
Cohesion: 0.13
Nodes (11): CustomAccountAdapter, CustomSocialAccountAdapter, Custom adapter to track email confirmations and welcome emails., Allow operators to pause new registrations without affecting existing users., Override to track email confirmation sends.          Args:             request:, Custom adapter to automatically generate usernames from email addresses     duri, Mirror email signup gating for social-account auto-signups., Automatically set username from email address before user creation.         Uses (+3 more)

### Community 22 - "API Key Hashing"
Cohesion: 0.25
Nodes (14): bool, str, generate_api_key(), get_api_key_prefix(), hash_api_key(), _hash_api_key_with_salt(), Generate an API key with a public lookup prefix and high-entropy secret., Return the public key prefix used for indexed lookup before hash verification. (+6 more)

### Community 23 - "Logging & Settings"
Cohesion: 0.15
Nodes (11): scrubbing_callback(), before_send(), CustomLoggingIntegration, build_redis_url(), extract_from_record(), str, Django settings for awesome_repos project.  Generated by 'django-admin startproj, Extract thread name and add them to the event dict. (+3 more)

### Community 24 - "Account Forms"
Cohesion: 0.20
Nodes (8): CustomLoginForm, CustomSignUpForm, Meta, ProfileUpdateForm, DivErrorList, ErrorList, LoginForm, SignupForm

### Community 25 - "views.py"
Cohesion: 0.27
Nodes (11): str, FormView, HttpResponse, _active_awesome_list_or_404(), awesome_list_request_client_ip(), awesome_list_request_rate_limit_key(), AwesomeListRequestView, queue_awesome_list_missing_repo_discovery() (+3 more)

### Community 26 - "DetailView"
Cohesion: 0.17
Nodes (9): DetailView, repository_history_chart_data(), repository_json_value_counts(), test_repository_history_chart_data_limits_latest_snapshots_chronologically(), test_repository_json_value_counts_aggregates_server_side(), test_repository_json_value_counts_can_scope_to_awesome_list(), test_repository_json_value_counts_rejects_unknown_fields(), AwesomeListDetailView (+1 more)

### Community 27 - "datetime"
Cohesion: 0.23
Nodes (13): datetime, attach_awesome_list_commit_count(), _commit_datetime(), dt(), fetch_github_commit_count_and_first_commit_at(), update_awesome_list_metadata(), _upsert_repository_metadata(), disable_repository_tagging() (+5 more)

### Community 28 - "repository-history-charts.js"
Cohesion: 0.23
Nodes (8): attachTooltip(), chartTheme(), emptyState(), expandedDateDomain(), expandedValueDomain(), initRepositoryHistoryCharts(), observeThemeChanges(), renderChart()

### Community 29 - "ApiConfig"
Cohesion: 0.18
Nodes (6): ApiConfig, AppConfig, CoreConfig, McpConfig, PagesConfig, ReposConfig

### Community 30 - "admin.py"
Cohesion: 0.22
Nodes (4): ReferrerBannerAdmin, Adds referrer banner to context. Priority order:     1. Exact match on ref or ut, referrer_banner(), ReferrerBanner

### Community 31 - "sync_awesome_repos.py"
Cohesion: 0.24
Nodes (8): Command, active_awesome_list_source_repository_name_set(), refresh_repositories(), sync_awesome_list(), test_detect_awesome_list_candidate_marks_tracked_source_repo(), test_detect_awesome_list_candidate_uses_preloaded_sources_without_queries(), test_refresh_repositories_defaults_to_full_sync(), test_sync_awesome_list_marks_empty_scan_as_error()

### Community 32 - "_get_api_key_from_headers()"
Cohesion: 0.20
Nodes (5): _get_api_key_from_headers(), HttpRequest, str, get_awesome_repos_logger(), This will add a `awesome_repos` prefix to logger for easy configuration.

### Community 33 - "context_processors.py"
Cohesion: 0.20
Nodes (4): available_social_providers(), chatwoot_settings(), Checks which social authentication providers are available.     Returns a list o, test_chatwoot_context_processor_exposes_widget_settings()

### Community 34 - "int"
Cohesion: 0.27
Nodes (10): int, _awesome_list_history_point(), awesome_list_repository_history_chart_data(), _format_delta(), _optional_delta(), repository_performance_summary(), test_awesome_list_repository_history_chart_data_aggregates_list_snapshots(), test_awesome_list_repository_history_chart_data_seeds_from_before_window() (+2 more)

### Community 35 - "test_signup_gating.py"
Cohesion: 0.33
Nodes (8): _account_adapter(), _social_account_adapter(), test_account_signup_adapter_can_pause_new_signups(), test_account_signup_adapter_defaults_open_for_signups(), test_account_signup_adapter_defaults_open_when_setting_is_absent(), test_social_signup_adapter_defaults_open_for_signups(), test_social_signup_adapter_defaults_open_when_setting_is_absent(), test_social_signup_adapter_uses_same_signup_gate()

### Community 37 - "admin.py"
Cohesion: 0.42
Nodes (5): EmailType, ProfileStates, EmailSent, Meta, ProfileStateTransition

### Community 38 - "copyAppJs()"
Cohesion: 0.28
Nodes (5): copyAppJs(), copyJsWithQueue(), jsWatcher, tailwindArgs, tailwindWatcher

### Community 39 - "ValueError"
Cohesion: 0.25
Nodes (8): ValueError, fetch_github_commit_count(), _last_page_from_link_header(), parse_github_repo_url(), test_fetch_github_commit_count_counts_single_unpaginated_page(), test_fetch_github_commit_count_requires_default_branch(), test_fetch_github_commit_count_uses_last_link_page(), test_parse_github_repo_url()

### Community 40 - "_apply_list_repository_state_f"
Cohesion: 0.38
Nodes (7): _apply_list_repository_state_filters(), _apply_repository_filters(), _apply_repository_state_filters(), _apply_repository_taxonomy_filters(), minimum_age_cutoff(), _positive_int_param(), test_minimum_age_cutoff_uses_calendar_years()

### Community 41 - "sitemaps.py"
Cohesion: 0.29
Nodes (4): Identify items that will be in the Sitemap          Returns:             List: u, Get location for each item in the Sitemap          Args:             item (str):, Generate Sitemap for the site, StaticViewSitemap

### Community 42 - "tasks.py"
Cohesion: 0.60
Nodes (5): int, str, track_event(), track_state_change(), try_create_posthog_alias()

### Community 43 - "0008_schedule_daily_budgeted_r"
Cohesion: 0.47
Nodes (5): create_daily_budgeted_repository_refresh_schedule(), Migration, next_daily_run(), next_monthly_run(), restore_monthly_repository_refresh_schedule()

### Community 44 - "0014_repository_awesome_list_d"
Cohesion: 0.53
Nodes (5): backfill_awesome_list_candidates(), detect_awesome_list_candidate(), extract_github_repos(), Migration, normalize_repository_tag()

### Community 45 - "0003_schedule_monthly_reposito"
Cohesion: 0.50
Nodes (3): create_monthly_repository_refresh_schedule(), Migration, next_monthly_run()

### Community 46 - "detect_awesome_list_candidate("
Cohesion: 0.40
Nodes (5): detect_awesome_list_candidate(), discover_missing_awesome_list_repositories(), extract_github_repos(), test_discover_missing_awesome_list_repositories_skips_existing_repos(), test_extract_github_repos_dedupes_and_skips_non_repo_paths()

### Community 47 - "Apple Touch Icon"
Cohesion: 0.70
Nodes (5): Apple Touch Icon, Awesome Repos Logo (Wordmark), Awesome Repos Mark, Awesome Repos Social Card, Awesome Repos Brand Identity

### Community 49 - "DJANGO_SETTINGS_MODULE"
Cohesion: 0.50
Nodes (4): DJANGO_SETTINGS_MODULE, PROJECT_NAME, wait_for_database(), entrypoint.sh script

### Community 50 - "_apply_list_repository_keyword"
Cohesion: 0.50
Nodes (4): _apply_list_repository_keyword_filter(), _apply_list_repository_taxonomy_filters(), awesome_list_repository_queryset(), _order_list_repositories()

### Community 52 - "Email Confirmation Message"
Cohesion: 0.50
Nodes (4): Email Confirmation Message, Signup Email Confirmation Message, Signup Email Confirmation Subject, Email Confirmation Subject

### Community 58 - "Pre-commit config"
Cohesion: 0.67
Nodes (3): Pre-commit config, djLint pre-commit hook, Ruff pre-commit hook

## Knowledge Gaps
- **102 isolated node(s):** `name`, `version`, `description`, `build`, `build:css` (+97 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **28 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `AwesomeList` connect `Test & Model Scaffolding` to `API Schema (Ninja In/Out)`, `Admin Panel Views & URLs`, `int`, `API Authentication`, `Awesome List Detail Tests`, `Repository Sync Tasks`, `GitHub Fetch Services`, `FastMCP Tools`, `Search Serializers`, `Awesome List Request Forms`, `views.py`, `DetailView`, `datetime`, `sync_awesome_repos.py`?**
  _High betweenness centrality (0.103) - this node is a cross-community bridge._
- **Why does `get_awesome_repos_logger()` connect `_get_api_key_from_headers()` to `API Schema (Ninja In/Out)`, `context_processors.py`, `Admin Panel Views & URLs`, `API Authentication`, `admin.py`, `AI Repo Tagging Agent`, `Repository Sync Tasks`, `tasks.py`, `Repository Embeddings`, `GitHub Fetch Services`, `Email Delivery Utils`, `Allauth Adapters`?**
  _High betweenness centrality (0.099) - this node is a cross-community bridge._
- **Why does `Repository` connect `Test & Model Scaffolding` to `API Schema (Ninja In/Out)`, `int`, `Awesome List Detail Tests`, `API Authentication`, `AI Repo Tagging Agent`, `Repository Sync Tasks`, `Repository Embeddings`, `GitHub Fetch Services`, `FastMCP Tools`, `Search Serializers`, `Awesome List Request Forms`, `views.py`, `DetailView`, `datetime`?**
  _High betweenness centrality (0.088) - this node is a cross-community bridge._
- **Are the 64 inferred relationships involving `Repository` (e.g. with `Agent` and `PlaceholderApiTests`) actually correct?**
  _`Repository` has 64 INFERRED edges - model-reasoned connections that need verification._
- **Are the 55 inferred relationships involving `AwesomeList` (e.g. with `PlaceholderApiTests` and `UserInfoApiUnitTests`) actually correct?**
  _`AwesomeList` has 55 INFERRED edges - model-reasoned connections that need verification._
- **Are the 5 inferred relationships involving `str` (e.g. with `AwesomeList` and `AwesomeListItem`) actually correct?**
  _`str` has 5 INFERRED edges - model-reasoned connections that need verification._
- **Are the 26 inferred relationships involving `RepositorySnapshot` (e.g. with `PlaceholderApiTests` and `UserInfoApiUnitTests`) actually correct?**
  _`RepositorySnapshot` has 26 INFERRED edges - model-reasoned connections that need verification._