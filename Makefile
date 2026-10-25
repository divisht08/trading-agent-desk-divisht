.PHONY: install dev-api dev-web demo test build

install:
	pip install -e ml -e "backend[dev]"
	cd frontend && npm ci

demo:            ## full app on synthetic prices, password "demo"
	cd frontend && npm run build
	cd backend && DATA_MODE=demo DATA_DIR=../data DASHBOARD_PASSWORD=demo SCHEDULER_ENABLED=false uvicorn app.main:app --port 8000

dev-api:         ## API with reload on :8000
	cd backend && uvicorn app.main:app --reload --port 8000

dev-web:         ## React dev server on :5173 (proxies /api to :8000)
	cd frontend && npm run dev

test:
	cd ml && python -m pytest -q
	cd backend && python -m pytest -q
	cd frontend && npm test && npm run typecheck

build:
	docker compose build
