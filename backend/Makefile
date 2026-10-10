.PHONY: install run smoke test docker-build up down
install:
	python -m pip install -r requirements.txt
run:
	python -m uvicorn vyom.api.app:app --reload --host 127.0.0.1 --port 8000
smoke:
	python scripts/smoke.py
test:
	python -m pytest -q tests
docker-build:
	docker compose build
up:
	docker compose up --build -d
down:
	docker compose down
