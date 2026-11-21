"""MagaBot — Telegram-бот-собеседник с характером (aiogram 3 + бесплатные модели OpenRouter)."""
import asyncio
import base64
import io
import logging
import os
import random
import re
import time
from collections import OrderedDict, deque
from datetime import datetime, timezone

import aiohttp
from aiogram import Bot, Dispatcher, F, types
from aiogram.enums import ChatAction, ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject, CommandStart

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("magabot")


def _env_list(name: str, default: str) -> list[str]:
    return [m.strip() for m in os.getenv(name, default).split(",") if m.strip()]


API_TOKEN = os.getenv("API_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
SITE_URL = os.getenv("SITE_URL", "https://github.com/dunaevx/magabot")

# Только бесплатные модели. Пробуются по очереди: если одна недоступна — берётся следующая.
# «openrouter/free» — автоматический выбор любой доступной бесплатной модели.
MODELS = _env_list("MODELS", "google/gemma-4-31b-it:free,nvidia/nemotron-3-ultra-550b-a55b:free,openrouter/free")
if os.getenv("MODEL") and os.getenv("MODEL") not in MODELS:  # старая переменная MODEL — первой в списке
    MODELS.insert(0, os.getenv("MODEL"))
VISION_MODELS = _env_list("VISION_MODELS",
                          "google/gemma-4-31b-it:free,nvidia/nemotron-3.5-super-vl:free,openrouter/free")
AUDIO_MODELS = _env_list("AUDIO_MODELS", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free")

MAX_HISTORY = int(os.getenv("MAX_HISTORY", 14))          # реплик собеседника и Маги (без системного промпта)
MAX_DIALOGS = int(os.getenv("MAX_DIALOGS", 2000))        # сколько диалогов держать в памяти
GROUP_CONTEXT = int(os.getenv("GROUP_CONTEXT", 20))      # сколько последних сообщений группы видит Мага
COOLDOWN_SECONDS = float(os.getenv("COOLDOWN_SECONDS", 2))
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", 60))
DAILY_LIMIT_USER = int(os.getenv("DAILY_LIMIT_USER", 20))    # запросов в сутки от одного человека
DAILY_LIMIT_CHAT = int(os.getenv("DAILY_LIMIT_CHAT", 100))   # запросов в сутки на один чат
INTERJECT_CHANCE = float(os.getenv("INTERJECT_CHANCE", 0.03))  # шанс, что Мага сам встрянет в разговор
INTERJECT_COOLDOWN = float(os.getenv("INTERJECT_COOLDOWN", 900))
MAX_VOICE_SECONDS = int(os.getenv("MAX_VOICE_SECONDS", 60))
MAX_PHOTO_BYTES = 4 * 1024 * 1024
TG_LIMIT = 4000                                           # лимит Telegram — 4096 символов

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
THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

dp = Dispatcher()
session: aiohttp.ClientSession | None = None

# (chat_id, user_id) -> реплики; OrderedDict работает как LRU, чтобы память не росла бесконечно
conversation_history: "OrderedDict[tuple, list]" = OrderedDict()
group_buffer: dict[int, deque] = {}          # chat_id -> последние сообщения группы
locks: dict[tuple, asyncio.Lock] = {}
last_request: dict[tuple, float] = {}
last_interject: dict[int, float] = {}
usage: dict[tuple, int] = {}                 # (день, "u"/"c", id) -> число запросов


class ModelError(Exception):
    def __init__(self, message: str, rate_limited: bool = False):
        super().__init__(message)
        self.rate_limited = rate_limited


# ====== Память ======

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


def remember_group_message(message: types.Message, text: str) -> None:
    if message.chat.type == ChatType.PRIVATE or not message.from_user:
        return
    buf = group_buffer.setdefault(message.chat.id, deque(maxlen=GROUP_CONTEXT))
    buf.append(f"{message.from_user.first_name}: {text[:300]}")


def group_context(chat_id: int) -> list[dict]:
    buf = group_buffer.get(chat_id)
    if not buf:
        return []
    return [{"role": "system", "content": "Последние сообщения в чате (для контекста):\n" + "\n".join(buf)}]


# ====== Лимиты ======

def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def check_and_count(chat_id: int, user_id: int) -> str | None:
    """Учитывает запрос. Возвращает текст отказа, если лимит исчерпан."""
    day = _today()
    for key in [k for k in usage if k[0] != day]:
        del usage[key]
    ukey, ckey = (day, "u", user_id), (day, "c", chat_id)
    if usage.get(ukey, 0) >= DAILY_LIMIT_USER:
        return "Всё, брат, на сегодня ты своё отговорил. Завтра приходи."
    if usage.get(ckey, 0) >= DAILY_LIMIT_CHAT:
        return "Мага на сегодня устал, весь чат меня заговорил. Завтра продолжим."
    usage[ukey] = usage.get(ukey, 0) + 1
    usage[ckey] = usage.get(ckey, 0) + 1
    return None


# ====== Текст ======

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


def addressed_to_maga(message: types.Message, text: str, bot_id: int) -> tuple[bool, str]:
    """(адресовано ли Маге, текст без обращения)."""
    match = TRIGGER_RE.match(text)
    if match:
        return True, text[match.end():].strip()
    if message.chat.type == ChatType.PRIVATE:
        return True, text.strip()
    reply = message.reply_to_message
    if reply and reply.from_user and reply.from_user.id == bot_id:
        return True, text.strip()
    return False, text.strip()


def extract_question(message: types.Message, bot_id: int) -> str | None:
    """Текст вопроса, если сообщение адресовано Маге, иначе None."""
    ok, text = addressed_to_maga(message, message.text or "", bot_id)
    return text if ok else None


# ====== Модели ======

async def call_models(models: list[str], messages: list[dict], temperature: float = 0.9) -> str:
    """Пробует модели по очереди и возвращает первый непустой ответ."""
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": SITE_URL,
        "X-Title": "MagaBot",
    }
    rate_limited = False
    last_error = "нет моделей"
    for model in models:
        payload = {"model": model, "messages": messages, "temperature": temperature}
        try:
            async with session.post(OPENROUTER_URL, headers=headers, json=payload) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    rate_limited |= resp.status == 429
                    last_error = f"{model}: HTTP {resp.status} {body[:300]}"
                    log.warning("Модель не ответила: %s", last_error)
                    continue
                data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            last_error = f"{model}: {e!r}"
            log.warning("Ошибка запроса: %s", last_error)
            continue
        choices = data.get("choices") or []
        content = (choices[0].get("message") or {}).get("content") if choices else None
        content = THINK_RE.sub("", content or "").strip()
        if content:
            return content
        last_error = f"{model}: пустой ответ"
    raise ModelError(last_error, rate_limited)


async def ask_model(history: list, chat_id: int | None = None) -> str:
    context = group_context(chat_id) if chat_id is not None else []
    return await call_models(MODELS, [{"role": "system", "content": SYSTEM_PROMPT}, *context, *history])


def error_text(e: Exception) -> str:
    if isinstance(e, ModelError) and e.rate_limited:
        return "Бесплатные запросы на сегодня кончились, брат. Завтра поговорим."
    return "Связь пропала, брат. Попробуй ещё раз чуть позже."


async def reply_long(message: types.Message, text: str) -> None:
    try:
        # parse_mode=None: ответ модели может содержать <, > и &, которые сломали бы HTML-разметку
        for part in split_text(text):
            await message.reply(part, parse_mode=None)
    except TelegramAPIError as e:
        log.warning("Не удалось отправить ответ: %s", e)


# ====== Общий сценарий ответа ======

async def converse(message: types.Message, bot: Bot, question: str, *, image_url: str | None = None,
                   history_note: str | None = None) -> None:
    """Отвечает в диалоге и запоминает реплики. question — текст от собеседника."""
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
    refusal = check_and_count(message.chat.id, message.from_user.id)
    if refusal:
        await message.reply(refusal)
        return

    async with lock:
        history = get_history(key)
        name = message.from_user.first_name
        user_line = f"{name}: {question}"
        try:
            await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
            if image_url:
                content = [{"type": "text", "text": user_line},
                           {"type": "image_url", "image_url": {"url": image_url}}]
                messages = [{"role": "system", "content": SYSTEM_PROMPT}, *group_context(message.chat.id),
                            *history, {"role": "user", "content": content}]
                answer = await call_models(VISION_MODELS, messages)
            else:
                answer = await ask_model([*history, {"role": "user", "content": user_line}], message.chat.id)
        except Exception as e:  # noqa: BLE001 — любая ошибка модели не должна ронять бота
            log.warning("Ошибка ответа: %s", e)
            await message.reply(error_text(e))
            return

        history.append({"role": "user", "content": f"{name}: {history_note or question}"})
        history.append({"role": "assistant", "content": answer})
        del history[:-MAX_HISTORY]  # системный промпт хранится отдельно и не обрезается
        if message.chat.type != ChatType.PRIVATE:
            group_buffer.setdefault(message.chat.id, deque(maxlen=GROUP_CONTEXT)).append(f"Мага: {answer[:300]}")
        await reply_long(message, answer)


async def one_shot(message: types.Message, bot: Bot, task: str) -> None:
    """Разовая просьба (тост, совет, подколка) — без записи в историю диалога."""
    if not message.from_user:
        return
    refusal = check_and_count(message.chat.id, message.from_user.id)
    if refusal:
        await message.reply(refusal)
        return
    try:
        await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
        answer = await call_models(MODELS, [{"role": "system", "content": SYSTEM_PROMPT},
                                            {"role": "user", "content": task}])
    except Exception as e:  # noqa: BLE001
        log.warning("Ошибка ответа: %s", e)
        await message.reply(error_text(e))
        return
    await reply_long(message, answer)


# ====== Команды ======

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer(
        "Салам! Я Мага.\n"
        "В группе пиши «Мага, ...» или отвечай на мои сообщения. В личке — просто пиши.\n"
        "Понимаю фото и голосовые.\n\n"
        "/toast [повод] — скажу тост\n"
        "/advice [тема] — дам совет\n"
        "/roast — ответь этой командой на чьё-то сообщение, и я его подколю\n"
        "/reset — забыть наш разговор"
    )


@dp.message(Command("reset"))
async def cmd_reset(message: types.Message):
    if message.from_user:
        conversation_history.pop((message.chat.id, message.from_user.id), None)
    await message.reply("Всё, забыл. Начинаем с чистого листа.")


@dp.message(Command("toast"))
async def cmd_toast(message: types.Message, bot: Bot, command: CommandObject):
    occasion = (command.args or "").strip()[:200] or "просто за хороших людей"
    await one_shot(message, bot, f"Скажи короткий кавказский тост (3–5 предложений). Повод: {occasion}.")


@dp.message(Command("advice"))
async def cmd_advice(message: types.Message, bot: Bot, command: CommandObject):
    topic = (command.args or "").strip()[:300] or "жизнь в целом"
    name = message.from_user.first_name if message.from_user else "брат"
    await one_shot(message, bot, f"{name} просит совет. Тема: {topic}. Дай один короткий дельный совет.")


@dp.message(Command("roast"))
async def cmd_roast(message: types.Message, bot: Bot, command: CommandObject):
    target = message.reply_to_message.from_user if message.reply_to_message else None
    if target and target.is_bot and target.id == bot.id:
        await message.reply("Себя подкалывать не буду, я идеальный.")
        return
    name = target.first_name if target else (command.args or "").strip()[:60]
    if not name:
        await message.reply("Ответь командой /roast на сообщение того, кого подколоть, или напиши /roast Имя.")
        return
    said = ""
    if message.reply_to_message and message.reply_to_message.text:
        said = f" Он только что написал: «{message.reply_to_message.text[:300]}»."
    await one_shot(message, bot,
                   f"Подколи по-дружески человека по имени {name}.{said} Коротко, смешно, без оскорблений "
                   "внешности, национальности и семьи — чтобы он сам посмеялся.")


# ====== Текст ======

@dp.message(F.text & ~F.text.startswith("/"))
async def handle_message(message: types.Message, bot: Bot):
    if not message.from_user or message.from_user.is_bot:
        return
    addressed, question = addressed_to_maga(message, message.text, bot.id)
    if not addressed:
        remember_group_message(message, message.text)
        await maybe_interject(message, bot)
        return
    if not question:
        await message.reply("Ну? Говори, чего хотел.")
        return
    remember_group_message(message, message.text)
    await converse(message, bot, question)


async def maybe_interject(message: types.Message, bot: Bot) -> None:
    """Иногда Мага сам вставляет реплику в разговор группы."""
    chat_id = message.chat.id
    buf = group_buffer.get(chat_id)
    if INTERJECT_CHANCE <= 0 or not buf or len(buf) < 5:
        return
    now = time.monotonic()
    if now - last_interject.get(chat_id, -INTERJECT_COOLDOWN) < INTERJECT_COOLDOWN:
        return
    if random.random() >= INTERJECT_CHANCE:
        return
    last_interject[chat_id] = now
    if check_and_count(chat_id, 0):
        return
    try:
        answer = await call_models(MODELS, [
            {"role": "system", "content": SYSTEM_PROMPT}, *group_context(chat_id),
            {"role": "user", "content": "Вставь в этот разговор одну короткую реплику от себя, по теме, с юмором."}])
    except Exception as e:  # noqa: BLE001
        log.info("Не удалось встрять в разговор: %s", e)
        return
    buf.append(f"Мага: {answer[:300]}")
    await reply_long(message, answer)


# ====== Фото ======

async def download_b64(bot: Bot, file_id: str) -> str:
    buf = io.BytesIO()
    await bot.download(file_id, destination=buf)
    return base64.b64encode(buf.getvalue()).decode()


@dp.message(F.photo)
async def handle_photo(message: types.Message, bot: Bot):
    if not message.from_user or message.from_user.is_bot:
        return
    addressed, caption = addressed_to_maga(message, message.caption or "", bot.id)
    if not addressed:
        remember_group_message(message, f"[фото] {message.caption or ''}")
        return
    photo = message.photo[-1]
    if photo.file_size and photo.file_size > MAX_PHOTO_BYTES:
        await message.reply("Слишком тяжёлая картинка, брат. Сожми и скинь ещё раз.")
        return
    try:
        b64 = await download_b64(bot, photo.file_id)
    except TelegramAPIError as e:
        log.warning("Не удалось скачать фото: %s", e)
        await message.reply("Фото не открылось, скинь ещё раз.")
        return
    question = caption or "Что скажешь про это фото?"
    await converse(message, bot, question, image_url=f"data:image/jpeg;base64,{b64}",
                   history_note=f"[прислал фото] {caption}".strip())


# ====== Голосовые ======

async def transcribe(audio_b64: str) -> str:
    messages = [{"role": "user", "content": [
        {"type": "text", "text": "Распознай речь в этом аудио и выведи только текст сказанного, дословно, "
                                 "без пояснений."},
        {"type": "input_audio", "input_audio": {"data": audio_b64, "format": "ogg"}},
    ]}]
    return await call_models(AUDIO_MODELS, messages, temperature=0)


@dp.message(F.voice)
async def handle_voice(message: types.Message, bot: Bot):
    if not message.from_user or message.from_user.is_bot:
        return
    reply = message.reply_to_message
    to_maga = message.chat.type == ChatType.PRIVATE or (reply and reply.from_user and reply.from_user.id == bot.id)
    if not to_maga:
        return
    if message.voice.duration and message.voice.duration > MAX_VOICE_SECONDS:
        await message.reply(f"Длинно, брат. Давай до {MAX_VOICE_SECONDS} секунд.")
        return
    try:
        await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
        text = await transcribe(await download_b64(bot, message.voice.file_id))
    except Exception as e:  # noqa: BLE001
        log.warning("Не удалось распознать голосовое: %s", e)
        await message.reply("Не разобрал, что ты сказал. Напиши текстом.")
        return
    text = TRIGGER_RE.sub("", text, count=1).strip()
    if not text:
        await message.reply("Тишина какая-то. Ещё раз скажи.")
        return
    remember_group_message(message, f"[голосовое] {text}")
    await converse(message, bot, text, history_note=f"[голосовое] {text}")


# ====== Запуск ======

async def on_startup(bot: Bot):
    global session
    session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT))
    log.info("Бот запущен. Модели: %s; фото: %s; голос: %s", MODELS, VISION_MODELS, AUDIO_MODELS)


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
