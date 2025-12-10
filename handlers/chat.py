from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message
from aiohttp import ClientSession
import asyncio
import logging
import asyncpg
from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, OPENROUTER_MODEL, ACCESS_DAYS, HTTP_REFERER, X_TITLE  # Добавь в config, если нет
from db import register_user, is_group_paid, get_group_remaining_days, get_history, save_to_history, is_user_paid

logger = logging.getLogger(__name__)
router = Router()

SYSTEM_PROMPT = """Ты из Дагестана, тебя зовут Мага. Отвечай коротко, резко, немного токсично,
по существу и лаконично, с кавказским стилем. Используешь простые слова,
немного юмора и сарказма. БЕЗ МАТА и без религиозной тематики."""

async def generate_response(
    session: ClientSession, 
    messages: list, 
    user_id: int
) -> str:
    """
    Универсальная генерация: OpenRouter с retry на 429, логами ошибок.
    Возвращает ответ или fallback в стиле Мага.
    """
    payload = {
        "model": OPENROUTER_MODEL,
        "messages": messages,
        "max_tokens": 300,  # Больше для норм ответов (экономь на истории)
        "temperature": 0.8,
    }
    
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    if HTTP_REFERER:  # Optional для ранкинга
        headers["HTTP-Referer"] = HTTP_REFERER
    if X_TITLE:
        headers["X-Title"] = X_TITLE
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            async with asyncio.timeout(30):  # 30s на весь запрос (asyncio.timeout в 3.11+)
                async with session.post(OPENROUTER_BASE_URL, headers=headers, json=payload) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        answer = data['choices'][0]['message']['content'].strip()
                        logger.info(f"OpenRouter OK для {user_id}: {answer[:50]}...")
                        return answer
                    elif resp.status == 429:
                        wait = 2 ** attempt  # Backoff: 1, 2, 4s
                        logger.warning(f"Rate-limit 429 для {user_id}, ждём {wait}s (попытка {attempt+1})")
                        await asyncio.sleep(wait)
                        continue
                    else:
                        error_text = await resp.text()
                        logger.error(f"OpenRouter {resp.status} для {user_id}: {error_text}")
                        if resp.status == 401:
                            return "Ключ API херня. Админ, проверь OPENROUTER_API_KEY."
                        elif resp.status == 400:
                            return "Модель или запрос кривой. Не тупи, админ пофиксит."
                        else:
                            return "Связь фигня. Перезагрузись, или жди — серверы не железные."
        except asyncio.TimeoutError:
            logger.error(f"Timeout 30s для {user_id}")
            return "Таймаут, брат. API спит? Жди минуту."
        except Exception as e:
            logger.error(f"OpenRouter exception для {user_id}: {e}")
            return "Что-то сломалось в API. Не ной, пиши админу."
    
    return "Лимит спама, подожди 5 мин. Не дави на газ."

@router.message(CommandStart())
async def cmd_start(message: Message, pool: asyncpg.Pool):
    """Старт: Регистрация и приветствие в стиле Мага."""
    user_id = message.from_user.id
    username = message.from_user.username or "аноним"
    await register_user(pool, user_id, username)
    
    if message.chat.type in ("group", "supergroup"):
        text = f"Эй, банда! Я Мага. Зовите 'Мага,' — отвечу, если группа заплатила. /pay — один платит за всех. /status — чек."
    else:
        text = f"Эй, братан! Я Мага. Пиши 'Мага,' — отвечу в ЛС (плати /pay за {ACCESS_DAYS} дней). /status — чек."
    await message.answer(text)
    logger.info(f"Старт для {user_id} ({username}) в {message.chat.type}")

@router.message(F.chat.type.in_({"group", "supergroup"}), F.text.startswith("Мага, "))
async def handle_mention(message: Message, pool: asyncpg.Pool, session: ClientSession):
    """Обработка упоминания в группе: Проверка группового доступа."""
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if not await is_group_paid(pool, chat_id):
        days = await get_group_remaining_days(pool, chat_id)
        if days > 0:
            await message.reply(f"В группе {days} дней осталось, но кончилось. Платите /pay заново, лентяи.")
        else:
            await message.reply(f"Группа не платит! /pay — 10 звёзд за {ACCESS_DAYS} дней всем. Один герой — все в теме.")
        return
    
    # Регистрируем юзера для истории (личной)
    await register_user(pool, user_id, message.from_user.username or "аноним")
    
    question = message.text[6:].strip()  # Убираем "Мага, "
    if not question:
        await message.reply("Что хотел, брат? Говори по делу, не тяни.")
        return
    
    # История + system
    history = await get_history(pool, user_id)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for role, content in history:
        messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": question})
    
    # Сохраняем вопрос заранее
    await save_to_history(pool, user_id, 'user', question)
    
    # Генерация
    answer = await generate_response(session, messages, user_id)
    await message.reply(answer)
    
    # Сохраняем ответ только если не fallback (чтоб не мусорить историю)
    if "фигня" not in answer.lower() and "сломалось" not in answer.lower():  # Простой хак; улучши по data['choices']
        await save_to_history(pool, user_id, 'assistant', answer)

# Для приватных чатов — личный доступ
@router.message(F.chat.type == "private", F.text.startswith("Мага, "))
async def handle_private_mention(message: Message, pool: asyncpg.Pool, session: ClientSession):
    """Приватный чат: Личный доступ."""
    user_id = message.from_user.id
    await register_user(pool, user_id, message.from_user.username or "аноним")
    
    if not await is_user_paid(pool, user_id):
        await message.reply(f"В ЛС плати сам! /pay — {ACCESS_DAYS} дней моих советов. Не жмоться.")
        return
    
    question = message.text[6:].strip()
    if not question:
        await message.reply("Чё молчишь? Пиши вопрос после 'Мага,'.")
        return
    
    # История + system
    history = await get_history(pool, user_id)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for role, content in history:
        messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": question})
    
    # Сохраняем вопрос
    await save_to_history(pool, user_id, 'user', question)
    
    # Генерация
    answer = await generate_response(session, messages, user_id)
    await message.reply(answer)
    
    # Сохраняем ответ, если success
    if "фигня" not in answer.lower() and "сломалось" not in answer.lower():
        await save_to_history(pool, user_id, 'assistant', answer)