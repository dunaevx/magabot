# Базовый образ: лёгкий Python 3.12, без лишнего мусора
FROM python:3.12-slim

# Метаданные: для ясности, не для понтов
LABEL maintainer="MagaBot Dev <maga@dagestan.ru>"
LABEL version="1.0"
LABEL description="Telegram M agaBot in Docker"

# Устанавливаем системные deps для asyncpg, aiohttp + ffmpeg для pydub (в одну строку для парсера)
RUN apt-get update && apt-get install -y libpq-dev gcc ffmpeg && rm -rf /var/lib/apt/lists/*

# Рабочая директория: чисто, как в горах
WORKDIR /app

# Копируем requirements и ставим Python deps: без кэша, чтоб свежо
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь код проекта: handlers, config, db, bot.py
COPY . .

# Env: UTF-8 для русского, без фигни
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Порт: не нужен для бота (polling), но на всякий
EXPOSE 8080

# Запуск: gunicorn? Нет, просто python bot.py — asyncio сам разберётся
CMD ["python", "bot.py"]