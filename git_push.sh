#!/bin/bash
cd /config
git add -A
if git diff --cached --quiet; then
  echo "Nothing to commit"
  exit 0
fi
git commit -m "HA config auto-sync $(date '+%Y-%m-%d %H:%M')"
git push origin main
