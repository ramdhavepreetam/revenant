.PHONY: dev test docs

# Editable installs in dependency order (deepest first) so each sibling name is
# already satisfied — plain pip never hits an index.
dev:
	pip install -e packages/nerva-core
	pip install -e packages/nerva-agent
	pip install -e packages/revenant-cli

test:
	pytest

docs:
	mkdocs build --strict
