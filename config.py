# Конфиг бота: хардкод для простоты (не коммить в git! Для прод — env-vars)

# Telegram Bot
BOT_TOKEN = "8256189886:AAEs4rtnkSizyLFMoQRNUrL84Ip1uOHgjTM"

# OpenRouter API (обновлено на free-модель 2025: Mistral Devstral — быстрая, 256K контекст)
OPENROUTER_API_KEY = "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"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "mistralai/devstral-2512:free"  # Фикс 400: актуальная free, вместо устаревшей Llama

# Optional headers для ранкинга (помогает избежать банов, не критичны)
HTTP_REFERER = "https://t.me/magabot"  # Твой бот-линк
X_TITLE = "МагаБот"

# DB (PostgreSQL: локалка)
DB_HOST = "localhost"
DB_PORT = 5432
DB_NAME = "maga_bot_db"
DB_USER = "postgres"
DB_PASS = "lol69056"

# Bot settings
ACCESS_DAYS = 30  # Дней премиум
MAX_HISTORY = 10  # Сообщений в истории
ADMIN_ID = 5653464572  # Твой ID
PAY_AMOUNT = 10
PAY_CURRENCY = 'XTR'

RESET_DB = False  # True — дроп таблиц при init (dev only)

# System prompt для Мага
SYSTEM_PROMPT = """
Ты из Дагестана, тебя зовут Мага. Отвечаешь коротко, резко, немного токсично,
по существу и лаконично, с кавказским стилем. Используешь простые слова,
немного юмора и сарказма. БЕЗ МАТА и без религиозной тематики.
"""

# Валидация
if not all([BOT_TOKEN, OPENROUTER_API_KEY, DB_PASS]):
    raise ValueError("Ключи в config.py кривые — бот не стартанёт.")