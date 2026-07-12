#!/bin/bash
# One-command prod deploy: gate, backup, push, restart, resume workers.
set -e
cd ~/Projects/photo-archive
git fetch -q origin develop
git merge origin/develop --no-edit || { echo "MERGE CONFLICT — resolve manually"; exit 1; }
cd web
PHOTOARCHIVE_SMOKE_MODE=1 .venv/bin/python -m pytest -q > /tmp/deploy-suite.log 2>&1
code=$?
tail -1 /tmp/deploy-suite.log
[ $code -ne 0 ] && { echo "SUITE RED — aborting deploy"; exit 1; }
cp photoarchive.db "photoarchive.db.pre-$(date +%m%d-%H%M).bak"
ls -t photoarchive.db.pre-*.bak | tail -n +6 | xargs -r rm -f
cd ..
git push -q origin main
sudo -n systemctl restart photoarchive
for i in $(seq 1 30); do sleep 5; c=$(curl -s -o /dev/null -w "%{http_code}" http://100.102.150.104:8000/ || echo 000); [ "$c" = "200" ] && echo "UP after ~$((i*5))s" && break; done
for e in cache/pregen/start geo/backfill/start sync/hash-backfill; do curl -s -X POST "http://100.102.150.104:8000/api/$e" -H "Content-Type: application/json" -d "{}" >/dev/null 2>&1; done
echo "DEPLOYED $(git log --oneline -1) — workers resumed (captions stay manual)"
