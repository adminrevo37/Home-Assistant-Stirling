#!/bin/bash
cd /config
git add -A
if git diff --cached --quiet; then
  echo "Nothing to commit"
  exit 0
fi
git commit -m "HA config auto-sync $(date '+%Y-%m-%d %H:%M')"
# Reconcile with any pushes to main (from the clone / Claude) before pushing so
# the nightly backup never fails on a non-fast-forward. Abort a conflicting
# rebase to leave the repo clean for the next deploy pull.
git pull --rebase origin main || git rebase --abort
git push origin main
