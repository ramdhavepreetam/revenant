.PHONY: dev test docs build check publish clean-dist

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

# --- PyPI release ----------------------------------------------------------
# Build sdists + wheels for all three packages into dist_pypi/.
build: clean-dist
	python3 -m build --outdir dist_pypi packages/nerva-core
	python3 -m build --outdir dist_pypi packages/nerva-agent
	python3 -m build --outdir dist_pypi packages/revenant-cli

# Validate the built distributions (metadata, README rendering) before upload.
check:
	python3 -m twine check dist_pypi/*

# Upload to PyPI. Requires a token in the environment / ~/.pypirc — run this
# yourself; it is intentionally NOT invoked by CI or by any other target.
#   TWINE_USERNAME=__token__ TWINE_PASSWORD=pypi-... make publish
publish:
	python3 -m twine upload dist_pypi/*

clean-dist:
	rm -rf dist_pypi
