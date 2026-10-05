#!/usr/bin/env bash
# Phase 0 read-only workspace discovery (no writes). Usage: PROFILE=my-workspace bash scripts/discover.sh
set -euo pipefail
P="${PROFILE:-my-workspace}"
echo "== identity =="
databricks current-user me -p "$P" -o json | jq '{userName, active, groups: [.groups[]?.display]}'
echo "== metastore =="
databricks metastores summary -p "$P" -o json | jq '{metastore_id,name,region,storage_root,delta_sharing_scope}'
echo "== catalogs (does smbc_genie already exist?) =="
databricks catalogs list -p "$P" -o json | jq -r '.[]|[.name,.catalog_type]|@tsv'
echo "== warehouses =="
databricks warehouses list -p "$P" -o json | jq -r '.[]|[.id,.name,.state,.warehouse_type,.enable_serverless_compute,.channel.name]|@tsv'
echo "== account groups =="
databricks groups list -p "$P" -o json | jq -r '.[].displayName' 2>/dev/null | head -40 || echo "(cannot list groups)"
echo "== genie spaces (is Genie enabled?) =="
databricks genie list-spaces -p "$P" -o json | jq '.spaces|length' 2>/dev/null || echo "(genie list unavailable)"
echo "== serving endpoints (FM endpoints behind ai_* functions) =="
databricks serving-endpoints list -p "$P" -o json | jq -r '.[].name' 2>/dev/null | head -20 || echo "(none / no access)"
echo "Done. SQL probes (current_version, ai_analyze_sentiment, ai_forecast) run in the Phase 2 smoke test."
