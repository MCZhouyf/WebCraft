#!/usr/bin/env bash
set -euo pipefail
WC_PACKAGE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
WC_OUTPUT="${WC_REPLAY_OUTPUT:-$WC_PACKAGE_ROOT/runs/reproduction}"
WC_ENV="${WC_REPLAY_VENV:-$WC_PACKAGE_ROOT/.venv}"
export PYTHONHASHSEED=0 PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
export CUDA_VISIBLE_DEVICES='' OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
unset PYTHONPATH
if [[ ! -f "$WC_PACKAGE_ROOT/assets/restricted/evidence/main.jsonl.gz" || ! -f "$WC_PACKAGE_ROOT/assets/frozen/main.jsonl.gz" || ! -f "$WC_PACKAGE_ROOT/assets/frozen/cp_rag.jsonl.gz" ]]; then
    if [[ -z "${WC_REVIEW_PASSWORD_FILE:-}" ]]; then
        echo 'Set WC_REVIEW_PASSWORD_FILE to the reviewer password file supplied through the submission system.' >&2
        exit 2
    fi
    openssl enc -d -aes-256-cbc -pbkdf2 \
        -in "$WC_PACKAGE_ROOT/corpus/encrypted/w9_replay_restricted.tar.gz.enc" \
        -pass "file:$WC_REVIEW_PASSWORD_FILE" | tar xz -C "$WC_PACKAGE_ROOT"
fi
if [[ ! -x "$WC_ENV/bin/python" ]]; then
    python3 -m venv "$WC_ENV"
    "$WC_ENV/bin/python" -m pip --isolated install --no-index \
        --find-links "$WC_PACKAGE_ROOT/wheelhouse" -r "$WC_PACKAGE_ROOT/requirements.lock"
fi
cd "$WC_PACKAGE_ROOT"
nice -n 19 "$WC_ENV/bin/python" -I repro/test_package.py
for WC_SCOPE in main ext_v2 cds; do
    nice -n 19 "$WC_ENV/bin/python" -I repro/runtime.py --scope "$WC_SCOPE" --output "$WC_OUTPUT"
done
