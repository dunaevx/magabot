"""MagaBot — Telegram-бот-собеседник с характером (aiogram 3 + OpenRouter)."""
import asyncio
import logging
import os
import re
import time
from collections import OrderedDict

import aiohttp
from aiogram import Bot, Dispatcher, F, types
from aiogram.enums import ChatAction, ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("magabot")

API_TOKEN = os.getenv("API_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = os.getenv("MODEL", "meituan/longcat-flash-chat:free")
SITE_URL = os.getenv("SITE_URL", "https://github.com/dunaevx/magabot")

MAX_HISTORY = int(os.getenv("MAX_HISTORY", 14))         # реплик пользователя и бота (без системного промпта)
MAX_DIALOGS = int(os.getenv("MAX_DIALOGS", 2000))       # сколько диалогов держать в памяти
COOLDOWN_SECONDS = float(os.getenv("COOLDOWN_SECONDS", 2))
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", 60))
TG_LIMIT = 4000                                         # лимит Telegram — 4096 символов

SYSTEM_PROMPT = (
    "Ты из Дагестана, тебя зовут Мага. "
    "Отвечаешь коротко, резко, немного токсично, по существу и лаконично, с кавказским стилем. "
    "Используешь простые слова, немного юмора и сарказма. "
    "Не используй сложные термины и длинные предложения. "
    "Без мата и без религиозной тематики. "
    "Перед репликой собеседника указано его имя — можешь обращаться к нему по имени."
)

# «Мага, ...», «мага ...», «Мага!» — но не «магазин»
TRIGGER_RE = re.compile(r"^\s*мага\b[\s,.:!?-]*", re.IGNORECASE)

dp = Dispatcher()
session: aiohttp.ClientSession | None = None

# (chat_id, user_id) -> список реплик; OrderedDict работает как LRU, чтобы память не росла бесконечно
conversation_history: "OrderedDict[tuple, list]" = OrderedDict()
locks: dict[tuple, asyncio.Lock] = {}
last_request: dict[tuple, float] = {}


def get_history(key: tuple) -> list:
    if key in conversation_history:
        conversation_history.move_to_end(key)
    else:
        conversation_history[key] = []
        while len(conversation_history) > MAX_DIALOGS:
            old_key, _ = conversation_history.popitem(last=False)
            locks.pop(old_key, None)
            last_request.pop(old_key, None)
    return conversation_history[key]


def get_lock(key: tuple) -> asyncio.Lock:
    if key not in locks:
        locks[key] = asyncio.Lock()
    return locks[key]


def split_text(text: str, limit: int = TG_LIMIT) -> list[str]:
    parts = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        parts.append(text[:cut])
        text = text[cut:].lstrip()
    if text:
        parts.append(text)
    return parts


def extract_question(message: types.Message, bot_id: int) -> str | None:
    """Возвращает текст вопроса, если сообщение адресовано Маге, иначе None."""
    text = message.text or ""
    match = TRIGGER_RE.match(text)
    if match:
        return text[match.end():].strip()
    if message.chat.type == ChatType.PRIVATE:
        return text.strip()
    reply = message.reply_to_message
    if reply and reply.from_user and reply.from_user.id == bot_id:
        return text.strip()
    return None


async def ask_model(history: list) -> str:
    payload = {
        "model": MODEL,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}, *history],
        "temperature": 0.9,
    }
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": SITE_URL,
        "X-Title": "MagaBot",
    }
    async with session.post(OPENROUTER_URL, headers=headers, json=payload) as resp:
        if resp.status != 200:
            body = await resp.text()
            raise RuntimeError(f"OpenRouter {resp.status}: {body[:500]}")
        data = await resp.json()
    choices = data.get("choices") or []
    content = (choices[0].get("message") or {}).get("content") if choices else None
    return (content or "").strip()


# ====== Команды ======

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer(
        "Салам! Я Мага.\n"
        "В группе пиши «Мага, ...» или отвечай на мои сообщения. В личке — просто пиши.\n"
        "/reset — забыть наш разговор."
    )


@dp.message(Command("reset"))
async def cmd_reset(message: types.Message):
    if message.from_user:
        conversation_history.pop((message.chat.id, message.from_user.id), None)
    await message.reply("Всё, забыл. Начинаем с чистого листа.")


# ====== Обработка текстов ======

@dp.message(F.text & ~F.text.startswith("/"))
async def handle_message(message: types.Message, bot: Bot):
    if not message.from_user or message.from_user.is_bot:
        return
    question = extract_question(message, bot.id)
    if question is None:
        return
    if not question:
        await message.reply("Ну? Говори, чего хотел.")
        return

    key = (message.chat.id, message.from_user.id)
    now = time.monotonic()
    if now - last_request.get(key, 0) < COOLDOWN_SECONDS:
        await message.reply("Э, не гони, брат. Дай ответить.")
        return
    last_request[key] = now

    lock = get_lock(key)
    if lock.locked():
        await message.reply("Подожди, ещё прошлое не дописал.")
        return

    async with lock:
        history = get_history(key)
        history.append({"role": "user", "content": f"{message.from_user.first_name}: {question}"})
        try:
            await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
            answer = await ask_model(history)
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError) as e:
            log.warning("Ошибка запроса к модели: %s", e)
            history.pop()  # не оставляем вопрос без ответа в истории
            await message.reply("Связь пропала, брат. Попробуй ещё раз чуть позже.")
            return

        if not answer:
            history.pop()
            await message.reply("Я не понял, ле, повтори.")
            return

        history.append({"role": "assistant", "content": answer})
        del history[:-MAX_HISTORY]  # системный промпт хранится отдельно и не обрезается

        try:
            # parse_mode=None: ответ модели может содержать <, > и &, которые сломали бы HTML-разметку
            for part in split_text(answer):
                await message.reply(part, parse_mode=None)
        except TelegramAPIError as e:
            log.warning("Не удалось отправить ответ: %s", e)


# ====== Запуск ======

async def on_startup(bot: Bot):
    global session
    session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT))
    log.info("Бот запущен, модель: %s", MODEL)


async def on_shutdown(bot: Bot):
    if session:
        await session.close()
    log.info("Бот остановлен.")


async def main():
    missing = [name for name in ("API_TOKEN", "OPENROUTER_API_KEY") if not os.getenv(name)]
    if missing:
        raise SystemExit("Не заданы переменные окружения: " + ", ".join(missing))
    bot = Bot(token=API_TOKEN)
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
