.PHONY: up down logs test clean

up: .env
	docker compose up --build -d
	@echo Frontend http://localhost:3000
	@echo Backend http://localhost:8000

.env:
	cp .env.example .env
	@echo Set two real keys in .env to enable live chat and pass the backend health check.

down:
	docker compose down

logs:
	docker compose logs -f

test:
	cd backend && python -m pytest -q

clean:
	docker compose down -v
