#!/usr/bin/env bash
# Runs the JS UI regression harness under JavaScriptCore (jsc) — no
# node needed. Copies the current app sources to a temp dir, stubs the
# browser APIs (tests/js_harness/stubs.mjs), and executes every check.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
JSC="${JSC:-/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/app"
cp -R "$ROOT/src/payload/web/static/js" "$TMP/app/js"
cp "$ROOT/src/payload/web/static/app.js" "$TMP/app/app.js"
cp "$ROOT/tests/js_harness/stubs.mjs" "$TMP/stubs.mjs"
cp "$ROOT/tests/js_harness/harness_files.mjs" "$TMP/harness_files.mjs"
cd "$TMP"
"$JSC" -m harness_files.mjs
