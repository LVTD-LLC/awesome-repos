%:
	@:

PYSCN_VERSION ?= 1.24.0
PYSCN_PATHS ?= apps awesome_repos manage.py

serve:
	docker compose -f docker-compose-local.yml up -d --build
	docker compose -f docker-compose-local.yml logs -f backend

shell:
	docker compose -f docker-compose-local.yml run --rm backend uv run --no-sync python ./manage.py shell_plus --ipython

manage:
	docker compose -f docker-compose-local.yml run --rm backend uv run --no-sync python ./manage.py $(filter-out $@,$(MAKECMDGOALS))

makemigrations:
	docker compose -f docker-compose-local.yml run --rm backend uv run --no-sync python ./manage.py makemigrations

migrate:
	docker compose -f docker-compose-local.yml run --rm backend uv run --no-sync python ./manage.py migrate

test:
	docker compose -f docker-compose-local.yml run --rm backend uv run --no-sync pytest $(filter-out $@,$(MAKECMDGOALS))

pyscn-check:
	uvx pyscn@$(PYSCN_VERSION) check --skip-clones $(PYSCN_PATHS)

pyscn-analyze:
	uvx pyscn@$(PYSCN_VERSION) analyze --no-open $(PYSCN_PATHS)

restart-worker:
	docker compose -f docker-compose-local.yml up -d workers --force-recreate
