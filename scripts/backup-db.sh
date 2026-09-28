#!/usr/bin/env bash
# Nightly backup of the Blanco OS database, 14-day rotation.
#
# Uses SQLite's own VACUUM INTO rather than copying the file: it takes a
# consistent snapshot while the service is running, and it checkpoints the WAL
# into the copy so the backup is a single self-contained file.
set -euo pipefail
cd "$(dirname "$0")/.."

DB="${BLANCO_OS_DB_PATH:-$PWD/data/blanco_os.db}"
DEST="${BLANCO_OS_BACKUP_DIR:-$PWD/data/backups}"
KEEP_DAYS=14

[ -f "$DB" ] || { echo "no database at $DB"; exit 1; }
mkdir -p "$DEST"

# Maintenance pass before the snapshot: fold the WAL back into the main file
# and truncate it, then defragment. The running service keeps one connection
# open forever, so the WAL needs an external nudge to actually shrink.
#
# Both steps want a write lock the service may briefly hold, so they are
# advisory: a locked database means we skip maintenance and still take the
# backup, rather than aborting the nightly run under `set -e`.
python3 - "$DB" <<'PY' || echo "maintenance skipped (database busy)"
import sqlite3, sys

conn = sqlite3.connect(sys.argv[1], timeout=30)
try:
    # (busy, log_pages, checkpointed_pages); busy != 0 means readers blocked it.
    busy, _, moved = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    print(f"checkpoint: {'blocked' if busy else 'ok'}, {moved} pages folded in")
    conn.isolation_level = None  # VACUUM cannot run inside a transaction.
    conn.execute("VACUUM")
    # VACUUM rewrites the whole database through the WAL, so it leaves a fresh
    # one behind. Fold that back in too, or the file we just shrank is replaced
    # by an equally large one.
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    print("vacuum: ok")
finally:
    conn.close()
PY

STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$DEST/blanco_os-$STAMP.db"

# sqlite3 CLI is not installed here; python has the same VACUUM INTO.
python3 -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute('VACUUM INTO ?', (sys.argv[2],)); c.close()" "$DB" "$OUT"
gzip -f "$OUT"

# Drop anything older than the retention window.
find "$DEST" -name 'blanco_os-*.db.gz' -mtime "+$KEEP_DAYS" -delete

echo "backed up -> $OUT.gz ($(du -h "$OUT.gz" | cut -f1))"
echo "retained:  $(find "$DEST" -name 'blanco_os-*.db.gz' | wc -l) snapshots"
