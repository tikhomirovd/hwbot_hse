#!/bin/bash
set -euo pipefail

BACKUP_DIR=/root/backups
DB=/root/hwbot/data/bot.db
STAMP=$(date +%F)

mkdir -p "$BACKUP_DIR"
/usr/bin/sqlite3 "$DB" ".backup $BACKUP_DIR/bot-$STAMP.db"
find "$BACKUP_DIR" -name 'bot-*.db' -mtime +30 -delete
