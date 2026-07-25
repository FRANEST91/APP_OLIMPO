#!/usr/bin/env bash
# Backup del volumen olimpo_data (olimpo.db) a un .db plano, vía el
# comando .backup de sqlite3 (copia consistente incluso con la app
# corriendo). Pensado para cron, no para correrlo a mano seguido.
#
# Uso: deploy/backup.sh
# Cron sugerido (diario a las 4am, guarda log aparte):
#   0 4 * * * /opt/olimpo/deploy/backup.sh >> /var/log/olimpo-backup.log 2>&1
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${OLIMPO_BACKUP_DIR:-$PROJECT_DIR/backups}"
STAMP="$(date +%Y%m%d_%H%M%S)"
TMP_NAME="backup_${STAMP}.db"

mkdir -p "$BACKUP_DIR"
cd "$PROJECT_DIR"

docker compose exec -T web sqlite3 /app/data/olimpo.db ".backup '/app/data/${TMP_NAME}'"
docker compose cp "web:/app/data/${TMP_NAME}" "$BACKUP_DIR/olimpo_${STAMP}.db"
docker compose exec -T web rm "/app/data/${TMP_NAME}"

# Conserva los últimos 14 días de backups
find "$BACKUP_DIR" -name 'olimpo_*.db' -mtime +14 -delete

echo "Backup OK: $BACKUP_DIR/olimpo_${STAMP}.db"
