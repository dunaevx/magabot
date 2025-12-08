from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from aiogram import F
from config import ADMIN_ID, ACCESS_DAYS
from db import set_user_paid, set_group_paid, get_remaining_days, get_group_remaining_days
import asyncpg

router = Router()

@router.message(Command("grant"))
async def cmd_grant_user(message: Message, pool: asyncpg.Pool):
    """Админ: /grant <user_id> <days> — ручной доступ юзеру."""
    if message.from_user.id != ADMIN_ID:
        await message.reply("Эй, не тронь чужое. Только админ раздаёт.")
        return
    
    try:
        parts = message.text.split()[1:]
        if len(parts) != 2:
            await message.reply("Формат: /grant <user_id> <days>. Не тупи, братан.")
            return
        user_id = int(parts[0])
        days = int(parts[1])
        if days <= 0:
            await message.reply("Дней >0, лентяй. Не минусуй доступ.")
            return
        
        from datetime import datetime, timedelta
        expiration = datetime.now() + timedelta(days=days)
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO users (user_id, username, paid_until) VALUES ($1, 'manual', $2) ON CONFLICT (user_id) DO UPDATE SET paid_until = $2",
                user_id, expiration
            )
        
        remaining = await get_remaining_days(pool, user_id)
        await message.reply(f"Доступ юзеру {user_id}: {remaining} дней. Раздал, как царь.")
    except ValueError:
        await message.reply("user_id и days — числа, не буквы. Попробуй заново.")
    except Exception as e:
        print(f"Grant error: {e}")
        await message.reply("Фигня случилась. Не ной, чекни логи.")

@router.message(Command("grant_group"))
async def cmd_grant_group(message: Message, pool: asyncpg.Pool):
    """Админ: /grant_group <chat_id> <days> — ручной доступ группе."""
    if message.from_user.id != ADMIN_ID:
        await message.reply("Не лезь, посторонний. Админ только.")
        return
    
    try:
        parts = message.text.split()[1:]
        if len(parts) != 2:
            await message.reply("Формат: /grant_group <chat_id> <days>. Чётко, брат.")
            return
        chat_id = int(parts[0])
        days = int(parts[1])
        if days <= 0:
            await message.reply("Дней положительно, не в минус.")
            return
        
        from datetime import datetime, timedelta
        expiration = datetime.now() + timedelta(days=days)
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO groups (chat_id, paid_until) VALUES ($1, $2) ON CONFLICT (chat_id) DO UPDATE SET paid_until = $2",
                chat_id, expiration
            )
        
        remaining = await get_group_remaining_days(pool, chat_id)
        await message.reply(f"Группа {chat_id}: {remaining} дней. Все в теме теперь.")
    except ValueError:
        await message.reply("chat_id и days — цифры, не иероглифы.")
    except Exception as e:
        print(f"Grant group error: {e}")
        await message.reply("Ошибка, админ. БД не в настроении.")