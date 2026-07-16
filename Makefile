.PHONY: frontend wheel check

# Build the SPA into src/typantic/web/web_dist/ (served by the API, baked into
# the wheel). `uv build` does NOT run npm, so build the frontend first.
frontend:
	cd web && npm ci && npm run build

# Build a wheel with the frontend included.
wheel: frontend
	uv build --wheel

# The Python check gate (ruff + mypy + 100%-coverage pytest).
check:
	uv run ruff check src tests
	uv run mypy src
	uv run pytest -q
