#!/bin/sh
# Regenerate tests/fixtures/python_export.json from the Python app in this
# repo (its export-config / export-ui / export-notifications), then run the
# Rust test that requires the Rust export to match it byte for byte.
#
#   rust/scripts/check-export.sh
set -eu
here=$(cd "$(dirname "$0")/.." && pwd)
repo=$(cd "$here/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

git -C "$repo" archive HEAD | tar -x -C "$work"
cp "$repo/doover_config.json" "$work/doover_config.json"
(
  cd "$work"
  uv sync -q
  uv run -q export-config
  uv run -q export-ui
  uv run -q export-notifications
)
python3 - "$work/doover_config.json" "$here/tests/fixtures/python_export.json" <<'PY'
import json, sys
app = json.load(open(sys.argv[1]))["analog_level_sensor"]
out = {k: app[k] for k in ("config_schema", "ui_schema", "notification_schema")}
with open(sys.argv[2], "w") as f:
    json.dump(out, f, indent=4, ensure_ascii=False)
    f.write("\n")
PY
cd "$here" && cargo test --quiet export_is_identical_to_the_python_apps
