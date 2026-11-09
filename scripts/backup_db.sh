#!/usr/bin/env bash
# ============================================================================
# Скрипт создания резервной копии PostgreSQL (Docker)
# Сохраняет сжатый дамп в папку backups/ и удаляет копии старше 14 дней.
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BACKUP_DIR="${PROJECT_DIR}/backups"

mkdir -p "$BACKUP_DIR"

TIMESTAMP="$(date +"%Y%m%d_%H%M%S")"
BACKUP_FILE="${BACKUP_DIR}/moysklad_backup_${TIMESTAMP}.sql.gz"

echo "📦 Создание резервной копии базы данных moysklad..."

docker exec -t moysklad_db pg_dump -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-moysklad}" --clean --if-exists | gzip > "$BACKUP_FILE"

echo "✅ Бэкап успешно создан: ${BACKUP_FILE} ($(du -h "$BACKUP_FILE" | cut -f1))"

# Ротация: удаление дампов старше 14 дней
echo "🧹 Удаление бэкапов старше 14 дней..."
find "$BACKUP_DIR" -type f -name "moysklad_backup_*.sql.gz" -mtime +14 -delete
echo "✨ Готово."

