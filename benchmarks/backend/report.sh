#!/usr/bin/env bash
# Benchmark base vs head locally and write the same report as the PR comment.
#
# Env:
#   BASE         branch, tag or commit to compare against (default: main)
#   HEAD_REF     branch, tag or commit to benchmark
#                (default: empty = the current working tree, incl. uncommitted changes)
#   CONCURRENCY  comma-separated concurrent users (default: 1,4,16,64)
#   DURATION     seconds per level (default: 20)
#   OUT          report file (default: benchmark-report.md)
set -euo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -- "${HERE}/../.." && pwd)"
BASE="${BASE:-main}"
HEAD_REF="${HEAD_REF:-}"
CONCURRENCY="${CONCURRENCY:-1,4,16,64}"
DURATION="${DURATION:-20}"
OUT="${OUT:-benchmark-report.md}"

cd "${ROOT}"
TMP="$(mktemp -d)"
WORKTREES=()
cleanup() {
  for wt in "${WORKTREES[@]}"; do git worktree remove --force "${wt}" || true; done
  rm -rf "${TMP}"
}
trap cleanup EXIT

checkout() {  # ref dir -> worktree with synced env
  git worktree add --quiet --detach "$2" "$1"
  WORKTREES+=("$2")
  uv sync --quiet --project "$2"
}

# https://github.com/<owner>/<repo> from the origin remote, for commit links.
repo_url="$(git remote get-url origin 2>/dev/null \
  | sed -E 's#^git@([^:]+):#https://\1/#; s#\.git$##')" || repo_url=""

checkout "${BASE}" "${TMP}/base"
base_sha="$(git -C "${TMP}/base" rev-parse HEAD)"

if [ -n "${HEAD_REF}" ]; then
  checkout "${HEAD_REF}" "${TMP}/head"
  head_root="${TMP}/head"
  head_name="${HEAD_REF}"
  head_sha="$(git -C "${head_root}" rev-parse HEAD)"
else
  head_root="${ROOT}"
  head_name="$(git rev-parse --abbrev-ref HEAD)"
  head_sha="$(git rev-parse HEAD)"
  if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    head_name="${head_name} (uncommitted changes)"
  fi
fi

echo "== base: ${BASE} (${base_sha:0:7})"
BENCH_PROJECT_ROOT="${TMP}/base" "${HERE}/run.sh" \
  -c "${CONCURRENCY}" -d "${DURATION}" --json "${TMP}/base.json"
echo "== head: ${head_name} (${head_sha:0:7})"
BENCH_PROJECT_ROOT="${head_root}" "${HERE}/run.sh" \
  -c "${CONCURRENCY}" -d "${DURATION}" --json "${TMP}/head.json"

uv run python "${HERE}/compare.py" \
  --component backend "${TMP}/base.json" "${TMP}/head.json" \
  --base-name "${BASE}" --base-sha "${base_sha}" \
  --head-name "${head_name}" --head-sha "${head_sha}" \
  --repo-url "${repo_url}" > "${OUT}"
echo "Report written to ${OUT}"
