import asyncio
import aiohttp
import os
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

print("API_TOKEN:", os.getenv('API_TOKEN'))
print("OPENROUTER_API_KEY:", os.getenv('OPENROUTER_API_KEY'))

API_TOKEN = os.getenv('API_TOKEN')
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# инициализация бота
bot = Bot(
    token=API_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

# история диалогов
conversation_history = {}

# ===== Обработка текстов =====
@dp.message(F.text.regexp(r"(?i)^мага,"))
async def handle_message(message: types.Message):
    chat_id = message.chat.id
    user_text = message.text[len("Мага,"):].strip()

    if chat_id not in conversation_history:
        conversation_history[chat_id] = [
            {
                "role": "system",
                "content": (
                    "Ты из Дагестана, тебя зовут Мага. "
                    "Отвечаешь коротко, резко, немного токсично, "
                    "по существу и лаконично, с характерным кавказским стилем общения. "
                    "Используешь простые слова и выражения, "
                    "иногда добавляешь юмор и сарказм. "
                    "Не используй сложные термины и длинные предложения. "
                    "Всегда будь прямолинейным и честным в своих ответах."
                ),
            }
        ]

    conversation_history[chat_id].append({"role": "user", "content": user_text})

    payload = {
        "model": "openrouter/sonoma-sky-alpha",
        "messages": conversation_history[chat_id],
        "temperature": 0.9,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json"
                },
                json=payload
            ) as resp:
                data = await resp.json()
                answer = data.get("choices", [{}])[0].get("message", {}).get("content", "Я не понял ле, повтори")
                await message.reply(answer)
                conversation_history[chat_id].append({"role": "assistant", "content": answer})
    except Exception as e:
        await message.reply(f"Ошибка: {str(e)}")


# ===== Обработка фото =====
@dp.message(F.photo)
async def handle_photo(message: types.Message):
    chat_id = message.chat.id

    # Берем фото максимального качества
    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    file_url = f"https://api.telegram.org/file/bot{API_TOKEN}/{file.file_path}"

    if chat_id not in conversation_history:
        conversation_history[chat_id] = [
            {
                "role": "system",
                "content": (
                    "Ты из Дагестана, тебя зовут Мага. "
                    "Отвечаешь коротко, по проще, по существу и лаконично, с характерным кавказским стилем общения. "
                    "Используешь простые слова и выражения, иногда добавляешь юмор и сарказм. "
                    "Не используй сложные термины и длинные предложения. "
                    "Каждый раз формулируй ответ по-новому, избегай повторений и одинаковых выражений. "
                    "Веди себя как реальный собеседник, а не как ИИ: не говори, что ты ИИ, не уточняй, что тебе нужно больше информации, не спрашивай, чего хотел пользователь, не добавляй фразы типа 'Что дальше?' или 'Что скажешь?'. "
                    "Просто отвечай как человек, продолжая разговор естественно, без лишних уточняющих вопросов."
                ),

            }
        ]

    # Добавляем картинку в историю сообщений
    conversation_history[chat_id].append({
        "role": "user",
        "content": [
            {"type": "text", "text": "Посмотри на фото:"},
            {"type": "image_url", "image_url": {"url": file_url}}
        ]
    })

    payload = {
        "model": "openrouter/sonoma-sky-alpha",
        "messages": conversation_history[chat_id]
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json"
                },
                json=payload
            ) as resp:
                data = await resp.json()
                answer = data.get("choices", [{}])[0].get("message", {}).get("content", "Нет ответа от ИИ.")
                await message.reply(answer)
                conversation_history[chat_id].append({"role": "assistant", "content": answer})
    except Exception as e:
        await message.reply(f"Ошибка: {str(e)}")


async def main():
    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

