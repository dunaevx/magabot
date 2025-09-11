# Используем официальный Python 3.11
FROM python:3.11-slim

# Рабочая директория внутри контейнера
WORKDIR /app

# Копируем файлы проекта
COPY . /app

# Устанавливаем зависимости
RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# Указываем переменные окружения для токенов (можно переопределять при запуске)

# Запуск бота
CMD ["python", "maga_bot.py"]

