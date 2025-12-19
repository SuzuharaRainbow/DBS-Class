#!/usr/bin/env bash
set -euo pipefail

# Verify paper-style microbenchmark metrics for one synthetic dataset.
# This script focuses on "strictly comparable" metrics (model counts, memory usage).
#
# Usage:
#   bash scripts/verify_paper_metrics_synthetic.sh syn_g10_l1
#
# Environment overrides:
#   LOOKUP_COUNT      (default: 1000)  - smaller is fine for model/memory verification
#   LID_THREADS       (default: auto)  - number of threads used by build/LID
#   DATE_TAG          (default: auto)  - output tag used in result filenames

dataset="${1:-syn_g10_l1}"
lookup="${LOOKUP_COUNT:-1000}"
threads="${LID_THREADS:-}"

if [[ -n "${threads}" ]]; then
  export LID_THREADS="${threads}"
fi

if [[ ! -f "datasets/${dataset}" ]]; then
  if [[ -f "datasets/syn/${dataset}" ]]; then
    ln -sfn "syn/${dataset}" "datasets/${dataset}"
  else
    echo "ERROR: datasets/${dataset} not found."
    echo "Hint: for GRE synthetic datasets, create symlinks like:"
    echo "  ln -sfn syn/${dataset} datasets/${dataset}"
    exit 1
  fi
fi

tag="${DATE_TAG:-$(date +%m%d)_paper_${dataset}}"
export DATE_TAG="${tag}"

mkdir -p "logs/verify/${dataset}"

echo "[1/2] disk_oriented (Table3-style models saving) dataset=${dataset} lookups=${lookup}"
DATASETS="${dataset}" LOOKUP_COUNT="${lookup}" \
  LAMBDA_LIST="1.016 1.5 2 3" TOTAL_RANGE_LIST="4 128 256 512" \
  bash RunOnSingleDisk.sh \
  >"logs/verify/${dataset}/disk_oriented_${tag}.log" 2>&1

echo "[2/2] compression (Table4/Fig9-style memory usage) dataset=${dataset} lookups=${lookup}"
DATASETS="${dataset}" LOOKUP_COUNT="${lookup}" \
  LAMBDA_LIST="1.05 5" TOTAL_RANGE_LIST="16 1024" \
  bash scripts/compression.sh ./datasets/ ./results "${lookup}" \
  >"logs/verify/${dataset}/compression_${tag}.log" 2>&1

echo "Done."
echo "  disk_oriented log: logs/verify/${dataset}/disk_oriented_${tag}.log"
echo "  compression log:   logs/verify/${dataset}/compression_${tag}.log"
