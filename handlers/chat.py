from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message
from aiohttp import ClientSession
import asyncio
import asyncpg
from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, OPENROUTER_MODEL, ACCESS_DAYS
from db import register_user, is_group_paid, get_group_remaining_days, get_history, save_to_history, is_user_paid

router = Router()

SYSTEM_PROMPT = """Ты из Дагестана, тебя зовут Мага. Отвечай коротко, резко, немного токсично,
по существу и лаконично, с кавказским стилем. Используешь простые слова,
немного юмора и сарказма. БЕЗ МАТА и без религиозной тематики."""

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

@router.message(F.chat.type.in_({"group", "supergroup"}), F.text.startswith("Мага, "))
async def handle_mention(message: Message, pool: asyncpg.Pool, session: ClientSession):
    """Обработка упоминания в группе: Проверка группового доступа."""
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if not await is_group_paid(pool, chat_id):
        days = await get_group_remaining_days(pool, chat_id)
        if days > 0:
            await message.reply("В группе срок кончился. Платите /pay заново, лентяи.")
        else:
            await message.reply(f"Группа не платит! /pay — 10 звёзд за {ACCESS_DAYS} дней всем. Один герой — все в теме.")
        return
    
    # Регистрируем юзера для истории (личной)
    await register_user(pool, user_id, message.from_user.username or "аноним")
    
    # Сохраняем вопрос пользователя
    question = message.text[6:].strip()  # Убираем "Мага, "
    await save_to_history(pool, user_id, 'user', question)
    
    try:
        # Получаем историю (личную)
        history = await get_history(pool, user_id)
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        for role, content in history:
            messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": question})
        
        # Запрос к OpenRouter
        async with session.post(OPENROUTER_BASE_URL, headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json"
        }, json={
            "model": OPENROUTER_MODEL,
            "messages": messages,
            "max_tokens": 150,  # Короткие ответы
            "temperature": 0.8  # Немного креатива
        }, timeout=10) as resp:
            if resp.status == 200:
                data = await resp.json()
                answer = data['choices'][0]['message']['content'].strip()
            else:
                answer = "Эй, связь барахлит. Попробуй позже, а то нервы не железные."
        
        await message.reply(answer)
        await save_to_history(pool, user_id, 'assistant', answer)
        
    except asyncio.TimeoutError:
        await message.reply("Таймаут, брат. API спит? Жди.")
    except Exception as e:
        print(f"Chat error: {e}")  # Лог
        await message.reply("Что-то сломалось. Не ной, пиши заново.")

# Для приватных чатов — личный доступ
@router.message(F.chat.type == "private", F.text.startswith("Мага, "))
async def handle_private_mention(message: Message, pool: asyncpg.Pool, session: ClientSession):
    """Приватный чат: Личный доступ."""
    user_id = message.from_user.id
    await register_user(pool, user_id, message.from_user.username or "аноним")
    
    if not await is_user_paid(pool, user_id):
        await message.reply(f"В ЛС плати сам! /pay — {ACCESS_DAYS} дней моих советов.")
        return
    
    question = message.text[6:].strip()
    await save_to_history(pool, user_id, 'user', question)
    
    try:
        history = await get_history(pool, user_id)
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        for role, content in history:
            messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": question})
        
        async with session.post(OPENROUTER_BASE_URL, headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json"
        }, json={
            "model": OPENROUTER_MODEL,
            "messages": messages,
            "max_tokens": 150,
            "temperature": 0.8
        }, timeout=10) as resp:
            if resp.status == 200:
                data = await resp.json()
                answer = data['choices'][0]['message']['content'].strip()
            else:
                answer = "Связь фигня. Перезагрузись."
        
        await message.reply(answer)
        await save_to_history(pool, user_id, 'assistant', answer)
        
    except Exception as e:
        print(f"Private chat error: {e}")
        await message.reply("Ошибка, чувак. Не дави на меня.")