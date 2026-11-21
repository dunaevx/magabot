FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Сначала зависимости — так Docker кэширует слой и не переустанавливает их при каждом изменении кода
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY maga_bot.py .

# Не запускаем бота от root
RUN useradd --create-home bot
USER bot

# Токены передаются при запуске:
# docker run -e API_TOKEN=... -e OPENROUTER_API_KEY=... magabot
CMD ["python", "maga_bot.py"]
