.PHONY: venv run test hash-password

venv: ## .venv с зависимостями и тестами
	python3 -m venv .venv && .venv/bin/pip install -q -r requirements-dev.txt

run: ## мотор локально на 127.0.0.1:4319, база в data/ (пароль — MOTOR_PASSWORD_HASH из окружения, если задан)
	MOTOR_SESSION_SECRET=$${MOTOR_SESSION_SECRET:-local-only-session-secret} .venv/bin/python -m motor

test:
	.venv/bin/pytest -q

hash-password: ## строка для MOTOR_PASSWORD_HASH
	@.venv/bin/python -m motor hash-password
