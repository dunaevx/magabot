import asyncpg
from aiogram import Router
from aiogram.types import Message, LabeledPrice, PreCheckoutQuery
from aiogram.filters import Command
from aiogram import F
from config import PAY_AMOUNT, PAY_CURRENCY, ACCESS_DAYS
from db import register_user, save_payment, update_payment_status, set_user_paid, set_group_paid, get_group_remaining_days, get_remaining_days


router = Router()

@router.message(Command("pay"))
@router.message(F.text.startswith("Оплата"))
async def cmd_pay(message: Message, pool: asyncpg.Pool):
    """Инициирует оплату. В группе — за всю группу."""
    user_id = message.from_user.id
    chat_id = message.chat.id if message.chat.type in ("group", "supergroup") else None
    await register_user(pool, user_id, message.from_user.username or "аноним")
    
    if chat_id:
        payload = f"group_pay_{chat_id}_{user_id}_{PAY_AMOUNT}"  # Групповой
        desc = f"10 Stars — и {ACCESS_DAYS} дней для всей группы. Один платит — все в теме."
        text = f"Плати за группу, герой. 10 звёзд — и все зовут меня. Или сиди в углу."
    else:
        payload = f"user_pay_{user_id}_{PAY_AMOUNT}"  # Личный
        desc = f"10 Stars — и {ACCESS_DAYS} дней болтовни в ЛС. Не жмись."
        text = f"Плати 10 звёзд за {ACCESS_DAYS} дней в ЛС. Или молчи, как рыба."
    
    payment_id = await save_payment(pool, user_id, chat_id, PAY_AMOUNT, payload)
    
    await message.bot.send_invoice(
        chat_id=message.chat.id,
        title="Доступ к МагаБоту",
        description=desc,
        payload=payload,
        provider_token="",  # Для Stars — пустой
        currency=PAY_CURRENCY,
        prices=[LabeledPrice(label="Доступ", amount=PAY_AMOUNT)],
        start_parameter="maga-pay"
    )
    await message.reply(text)

@router.message(Command("status"))
async def cmd_status(message: Message, pool: asyncpg.Pool):
    """Проверяет статус: групповой или личный."""
    user_id = message.from_user.id
    chat_id = message.chat.id if message.chat.type in ("group", "supergroup") else None
    
    if chat_id:
        days = await get_group_remaining_days(pool, chat_id)
        if days > 0:
            text = f"Группа в теме: {days} дней. Болтайте все!"
        elif days == 0:
            text = "В группе срок кончился. Платите заново, лентяи. /pay"
        else:
            text = "Группа не платит. /pay — и все счастливы."
    else:
        # Личный для ЛС
        days = await get_remaining_days(pool, user_id)  # Теперь определена!
        if days > 0:
            text = f"Твой доступ: {days} дней. Не трать зря!"
        elif days == 0:
            text = "Твой срок кончился. /pay заново."
        else:
            text = "Ты не платил. /pay — вперёд."
    await message.reply(text)

@router.pre_checkout_query()
async def pre_checkout(pre_checkout_q: PreCheckoutQuery, pool: asyncpg.Pool):
    """Проверяет pre-checkout (для Stars — минимальная)."""
    await pre_checkout_q.answer(ok=True)

@router.message(F.successful_payment)
async def successful_payment(message: Message, pool: asyncpg.Pool):
    """Обработка успешной оплаты: Личный или групповой."""
    user_id = message.from_user.id
    payload = message.successful_payment.invoice_payload
    
    # Фикс: Обнови последний pending по user_id
    async with pool.acquire() as conn:
        payment_id = await conn.fetchval(
            "SELECT id FROM payments WHERE user_id = $1 AND status = 'pending' ORDER BY created_at DESC LIMIT 1",
            user_id
        )
        if payment_id:
            await update_payment_status(pool, payment_id, 'success')
    
    if payload.startswith("group_pay_"):
        # Групповой: парсим chat_id
        parts = payload.split('_')
        chat_id = int(parts[2])
        await set_group_paid(pool, chat_id)
        days = ACCESS_DAYS
        text = f"Группа оплачена! {days} дней всем. Зовите меня, не стесняйтесь."
        # Бонус: можно notify_all в группе, но для простоты — ответ плательщику
    else:
        # Личный
        await set_user_paid(pool, user_id)
        days = ACCESS_DAYS
        text = f"Твой доступ: {days} дней. Болтай в ЛС."
    
    await message.reply(text)