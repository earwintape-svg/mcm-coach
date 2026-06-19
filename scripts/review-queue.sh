#!/usr/bin/env bash
# "What's waiting on me?" — branches not yet merged into main + open PRs.
git fetch -q origin 2>/dev/null || true
echo "== Branches not merged into main =="
git branch -a --no-merged main --format='%(refname:short)' 2>/dev/null | sort -u | while read -r b; do
  [ -n "$b" ] && printf "  %-30s %s\n" "$b" "$(git log -1 --format='%h %s' "$b" 2>/dev/null)"
done
echo
if command -v gh >/dev/null 2>&1; then
  echo "== Open PRs =="; gh pr list --state open 2>/dev/null || echo "  (gh not authenticated: gh auth login)"
else
  echo "== Open PRs =="; echo "  install gh for live PR list:  brew install gh && gh auth login"
fi
