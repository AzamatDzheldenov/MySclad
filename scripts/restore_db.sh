#!/usr/bin/env bash
# ============================================================================
# Скрипт восстановления базы данных PostgreSQL из резервной копии (Docker)
# Использование: ./scripts/restore_db.sh backups/moysklad_backup_YYYYMMDD_HHMMSS.sql.gz
# ============================================================================

set -euo pipefail

if [ -z "${1:-}" ]; then
  echo "❌ Ошибка: укажите путь к файлу бэкапа (.sql.gz)."
  echo "Пример: $0 backups/moysklad_backup_20261109_120000.sql.gz"
  exit 1
fi

BACKUP_FILE="$1"

if [ ! -f "$BACKUP_FILE" ]; then
  echo "❌ Файл не найден: $BACKUP_FILE"
  exit 1
fi

echo "⚠️ ВНИМАНИЕ: Восстановление перезапишет текущую базу данных moysklad!"
read -p "Вы уверены, что хотите продолжить? (y/N): " -r CONFIRM
if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
  echo "Отменено пользователем."
  exit 0
fi

echo "🔄 Восстановление базы данных из $BACKUP_FILE..."
gunzip -c "$BACKUP_FILE" | docker exec -i moysklad_db psql -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-moysklad}"

echo "✅ База данных успешно восстановлена из бэкапа!"

