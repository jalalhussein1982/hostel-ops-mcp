.PHONY: sync test fixtures stdio http inspector token

sync:
	uv sync

test:
	uv run pytest -q

fixtures:
	uv run python scripts/make_fixtures.py

stdio:
	uv run python -m hostel_ops_mcp.server --stdio

http:
	uv run python -m hostel_ops_mcp.server --http

inspector:
	npx @modelcontextprotocol/inspector uv run python -m hostel_ops_mcp.server --stdio

token:
	uv run python scripts/dev_token.py
