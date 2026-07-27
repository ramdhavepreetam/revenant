#!/usr/bin/env bash
# Build the PUBLIC (Revenant-only) docs site into ./site_public.
#
# Produces a companion-free docs tree: the private companion (aibot-app) pages and
# every link to them are stripped, so nothing about the companion is published.
# Used locally and by the docs-public CI workflow. Requires the 3 public packages
# installed (nerva-core, nerva-agent, revenant-cli); aibot-app must NOT be needed.
set -euo pipefail
cd "$(dirname "$0")/.."

STAGE="$(mktemp -d)"
cp -r docs "$STAGE/docs"

# 1. Remove companion pages: guides, the AIBot-companion knowledge base, aibot_app.*
#    API pages, and the companion-facing tool page.
rm -f "$STAGE/docs/companion-agent.md" \
      "$STAGE/docs/companion-harness-plan.md" \
      "$STAGE/docs/knowledge-base.md" \
      "$STAGE/docs/api/aibot_companion_compiler.md" \
      "$STAGE/docs/api/aibot_companion_memory.md" \
      "$STAGE/docs/api/aibot_context.md" \
      "$STAGE/docs/api/aibot_summary.md" \
      "$STAGE/docs/api/aibot_personal_memory.md" \
      "$STAGE/docs/api/aibot_tts.md" \
      "$STAGE/docs/api/web_app.md" \
      "$STAGE/docs/api/agent_companion_tools.md"

# 2. Strip lines that link to the removed pages (markdown links + table rows).
COMPANION_RE='companion-agent|companion-harness|knowledge-base|aibot_companion|aibot_personal|aibot_context|aibot_summary|aibot_tts|web_app|agent_companion_tools'
for f in "$STAGE/docs/index.md" "$STAGE/docs/architecture.md" "$STAGE/docs/api/index.md"; do
  [ -f "$f" ] && grep -viE "\]\(($COMPANION_RE)" "$f" > "$f.tmp" && mv "$f.tmp" "$f"
done

# 3. Build with the public config pointed at the staged, filtered docs.
sed "s|docs_dir: docs|docs_dir: $STAGE/docs|" mkdocs.public.yml > "$STAGE/mkdocs.yml"
mkdocs build -f "$STAGE/mkdocs.yml" -d "$(pwd)/site_public" --strict

# 4. Safety net: fail if actual companion CODE/module paths leak into the built HTML.
#    (Incidental prose mentions of the companion in kept design docs — e.g. explaining
#    the harness's two front-ends — are acceptable; importable aibot_app.* paths and
#    the private companion source pages are not.)
if grep -rqiE 'aibot_app\.|api/aibot_companion|api/aibot_personal|api/web_app|companion-agent/' site_public/ 2>/dev/null; then
  echo "ERROR: companion code/paths leaked into the public site — aborting." >&2
  grep -rliE 'aibot_app\.|api/aibot_companion|api/web_app|companion-agent/' site_public/ | head >&2
  exit 1
fi

rm -rf "$STAGE"
echo "Public docs built into ./site_public (companion-free, verified)."
