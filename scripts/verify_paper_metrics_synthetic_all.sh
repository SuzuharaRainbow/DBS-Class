#!/usr/bin/env bash
set -euo pipefail

# One-shot sequential verification runner for the remaining 4 synthetic datasets.
#
# Default datasets:
#   syn_g10_l2 syn_g10_l4 syn_g12_l1 syn_g12_l4
#
# Usage:
#   bash scripts/verify_paper_metrics_synthetic_all.sh
#   LOOKUP_COUNT=1000000 bash scripts/verify_paper_metrics_synthetic_all.sh
#   LID_THREADS=16 bash scripts/verify_paper_metrics_synthetic_all.sh
#   DATASETS="syn_g10_l2 syn_g12_l4" bash scripts/verify_paper_metrics_synthetic_all.sh
#
# Environment overrides:
#   DATASETS         - space-separated dataset list to run (default: 4 remaining)
#   LOOKUP_COUNT     - lookups per dataset (default: 1000000)
#   LID_THREADS      - thread count used by LID (default: auto-detect)
#   DATE_TAG_PREFIX  - prefix used to form DATE_TAG per dataset (default: <mmdd>_paper)
#   SKIP_IF_EXISTS   - if "1", skip dataset when run is complete (default: 1)

if [[ ! -x "./build/LID" ]]; then
  echo "ERROR: ./build/LID not found. Build first (e.g., mkdir -p build && cmake -S . -B build && cmake --build build -j)."
  exit 1
fi

default_datasets=("syn_g10_l2" "syn_g10_l4" "syn_g12_l1" "syn_g12_l4")
if [[ -n "${DATASETS:-}" ]]; then
  read -r -a datasets <<< "${DATASETS}"
else
  datasets=("${default_datasets[@]}")
fi

lookup="${LOOKUP_COUNT:-1000000}"
skip="${SKIP_IF_EXISTS:-1}"
tag_prefix="${DATE_TAG_PREFIX:-$(date +%m%d)_paper}"

mkdir -p "logs/verify"

is_complete() {
  local driver_log_path="$1"
  local disk_csv_path="$2"
  local comp_csv_path="$3"
  [[ -f "${disk_csv_path}" && -f "${comp_csv_path}" && -f "${driver_log_path}" ]] || return 1
  rg -q "^Done\\.$" "${driver_log_path}"
}

backup_if_exists() {
  local p="$1"
  if [[ -f "${p}" ]]; then
    mv -f "${p}" "${p}.incomplete.$(date +%Y%m%d_%H%M%S)"
  fi
}

total="${#datasets[@]}"
idx=0
for dataset in "${datasets[@]}"; do
  idx=$((idx + 1))
  tag="${tag_prefix}_${dataset}"

  disk_csv="results/diskOriented/res_${tag}_8B_fetch0_${dataset}.csv"
  comp_csv="results/compression/res_${tag}_8B_fetch0_${dataset}.csv"
  driver_log="logs/verify/${tag}_full_driver.log"

  if [[ "${skip}" == "1" ]] && is_complete "${driver_log}" "${disk_csv}" "${comp_csv}"; then
    echo "[${idx}/${total}] skip (exists) dataset=${dataset}"
    echo "  ${disk_csv}"
    echo "  ${comp_csv}"
    continue
  fi

  if [[ -f "${disk_csv}" || -f "${comp_csv}" || -f "${driver_log}" ]]; then
    echo "[${idx}/${total}] rerun (incomplete) dataset=${dataset} tag=${tag}"
    backup_if_exists "${disk_csv}"
    backup_if_exists "${comp_csv}"
    backup_if_exists "${driver_log}"
  fi

  echo "[${idx}/${total}] run dataset=${dataset} lookups=${lookup} tag=${tag}"
  DATE_TAG="${tag}" LOOKUP_COUNT="${lookup}" \
    bash scripts/verify_paper_metrics_synthetic.sh "${dataset}" 2>&1 | tee "${driver_log}"
done

echo "All done."
