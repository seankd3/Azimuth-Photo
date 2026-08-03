#!/bin/bash
cd "$(dirname "$0")"

# cumulative lines via shortstat (fast, one git log call)
git log --reverse --pretty=format:'C|%H|%ad' --date=format:%Y-%m-%d --shortstat > /tmp/shortstat_raw.txt

python3 - <<'EOF'
import re, csv

commits = []  # (hash, date, insertions, deletions)
cur = None
with open('/tmp/shortstat_raw.txt', encoding='utf-8') as f:
    for line in f:
        line = line.rstrip('\n')
        if line.startswith('C|'):
            _, h, d = line.split('|', 2)
            cur = {'hash': h, 'date': d, 'ins': 0, 'del': 0}
            commits.append(cur)
        elif 'changed' in line:
            m_ins = re.search(r'(\d+) insertion', line)
            m_del = re.search(r'(\d+) deletion', line)
            if cur is not None:
                cur['ins'] = int(m_ins.group(1)) if m_ins else 0
                cur['del'] = int(m_del.group(1)) if m_del else 0

running = 0
rows = []
for c in commits:
    running += c['ins'] - c['del']
    rows.append((c['date'], c['hash'], max(running,0)))

with open('/tmp/lines_cumulative.csv', 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['date','commit','lines'])
    w.writerows(rows)

print(len(rows), "commits processed")
EOF
