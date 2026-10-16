#!/data/data/com.termux/files/usr/bin/bash
# Daily auto24 check, run on the Android phone (Termux) by cron.
#   1. pull latest repo (searches.json may have changed)
#   2. scrape + diff (watcher.py)
#   3. commit/push state/listings.json
#   4. open a GitHub issue with report.md (if anything changed)
#      or an issue about the failure (if the scrape failed)
#
# Needs ~/.auto24.env with:
#   GITHUB_TOKEN=github_pat_...      (fine-grained, this repo only: Contents + Issues read/write)
#   GITHUB_REPO=japiho/auto24-watcher

set -u
cd "$(dirname "$0")"
LOG="$HOME/auto24-watcher.log"
exec >>"$LOG" 2>&1
echo "=== $(date '+%F %T') ==="

# shellcheck disable=SC1090
source "$HOME/.auto24.env"
API="https://api.github.com/repos/$GITHUB_REPO"

termux-wake-lock 2>/dev/null || true
trap 'termux-wake-unlock 2>/dev/null || true' EXIT

post_issue() {  # $1 = title, $2 = body file
  python - "$1" "$2" <<'PY' | curl -sS -X POST "$API/issues" \
      -H "Authorization: Bearer $GITHUB_TOKEN" \
      -H "Accept: application/vnd.github+json" \
      -d @- -o /dev/null -w "issue: HTTP %{http_code}\n"
import json, sys
print(json.dumps({"title": sys.argv[1], "body": open(sys.argv[2], encoding="utf-8").read(), "labels": ["auto24"]}))
PY
}

git pull -q --rebase || echo "git pull failed (continuing with local copy)"

if python watcher.py; then
  if ! git diff --quiet -- state/listings.json || ! git ls-files --error-unmatch state/listings.json >/dev/null 2>&1; then
    git add state/listings.json
    git commit -qm "Snapshot $(date +%F)"
    git push -q || echo "git push failed"
  fi
  if [ -f report.md ]; then
    post_issue "auto24 $(date +%d.%m.%Y)" report.md
  fi
else
  { echo "The phone run failed. Last log lines:"; echo '```'; tail -n 30 "$LOG"; echo '```'; } > "$HOME/auto24_err.md"
  post_issue "auto24 FAILED $(date +%d.%m.%Y)" "$HOME/auto24_err.md"
fi
