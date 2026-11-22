#!/usr/bin/env bash
# List the saved models (no argument), or promote one so inference trades on it: scripts/promote_model.sh <version>
set -euo pipefail
export AWS_PROFILE="${AWS_PROFILE:-secform4}"
bucket="${MODELS_BUCKET:-secform4strategy-data-$(aws sts get-caller-identity --query Account --output text)}"
models="s3://$bucket/models"
current=$(aws s3 cp "$models/latest.json" - 2>/dev/null | python3 -c 'import json, sys; print(json.load(sys.stdin)["version"])' 2>/dev/null || true)

if [[ $# -eq 0 ]]; then
  printf '%-18s %-26s %10s %10s %10s %8s\n' version trained_at train_rows model_mae zero_mae n_shorts
  for version in $(aws s3api list-objects-v2 --bucket "$bucket" --prefix models/ --delimiter / \
    --query 'CommonPrefixes[].Prefix' --output text | tr '\t' '\n' | sed -e '/^None$/d' -e 's|^models/||' -e 's|/$||'); do
    aws s3 cp "$models/$version/model.meta.json" - | python3 -c '
import json, sys
meta, (version, current) = json.load(sys.stdin), sys.argv[1:3]
model, zero = meta["metrics"]["model"], meta["metrics"].get("zero", {}).get("mae", float("nan"))
mark = "  <- promoted" if version == current else ""
print("%-18s %-26s %10d %10.5f %10.5f %8d%s" % (version, meta["trained_at"], meta["train_rows"], model["mae"], zero, model["n_shorts"], mark))
' "$version" "$current"
  done
  exit 0
fi

version=$1
aws s3api head-object --bucket "$bucket" --key "models/$version/model.meta.json" >/dev/null 2>&1 \
  || { echo "No saved model $version under $models" >&2; exit 1; }
aws s3api head-object --bucket "$bucket" --key "models/$version/model.joblib" >/dev/null 2>&1 \
  || { echo "Model $version has metadata but no model.joblib" >&2; exit 1; }
printf '{"version": "%s"}' "$version" | aws s3 cp - "$models/latest.json" --content-type application/json
echo "Promoted $version (was ${current:-none}); inference loads it on its next run."
