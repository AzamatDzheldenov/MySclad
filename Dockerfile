# ============================================================================
# Dockerfile для Backend (FastAPI) и Telegram-бота (aiogram 3.x)
# ============================================================================
FROM python:3.12-slim AS base

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Установка системных утилит (curl для healthcheck)
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Установка Python-зависимостей отдельным слоем для кэширования
COPY requirements.txt .
RUN pip install -r requirements.txt

# Копирование исходного кода приложения
COPY app ./app
COPY frontend ./frontend
COPY sql ./sql

# Создание директории для загрузок накладных и непривилегированного пользователя
RUN useradd --create-home --shell /bin/bash appuser \
    && mkdir -p /app/uploads/waybills \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

# Команда по умолчанию — запуск FastAPI (для контейнера bot переопределяется в docker-compose.yml)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--proxy-headers"]
