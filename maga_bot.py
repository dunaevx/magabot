import asyncio
import aiohttp
import os
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

API_TOKEN = os.getenv('API_TOKEN')
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


# Инициализация бота
bot = Bot(
    token=API_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

# Истории и замки
conversation_history = {}  # {(chat_id, user_id): [...]}
locks = {}  # {(chat_id, user_id): asyncio.Lock()}
MAX_HISTORY = 15

session: aiohttp.ClientSession = None  # будет создан при старте


def get_lock(chat_id, user_id):
    key = (chat_id, user_id)
    if key not in locks:
        locks[key] = asyncio.Lock()
    return locks[key]


def get_history(chat_id, user_id):
    key = (chat_id, user_id)
    if key not in conversation_history:
        conversation_history[key] = [
            {
                "role": "system",
                "content": (
                    "Ты из Дагестана, тебя зовут Мага. "
                    "Отвечаешь коротко, резко, немного токсично, "
                    "по существу и лаконично, с кавказским стилем. "
                    "Используешь простые слова, немного юмора и сарказма. "
                    "Не используй сложные термины и длинные предложения."
                ),
            }
        ]
    return conversation_history[key]


# ===== Обработка текстов =====
@dp.message(F.text.regexp(r"(?i)^мага,"))
async def handle_message(message: types.Message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    user_name = message.from_user.first_name
    user_text = message.text[len("Мага,"):].strip()

    async with get_lock(chat_id, user_id):
        history = get_history(chat_id, user_id)
        history.append({"role": "user", "content": f"{user_name}: {user_text}"})
        history[:] = history[-MAX_HISTORY:]

        payload = {
            "model": "openrouter/sonoma-sky-alpha",
            "messages": history,
            "temperature": 0.9,
        }

        try:
            async with session.post(
                OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json"
                },
                json=payload
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    await message.reply(f"Ошибка API: {resp.status}\n{text}")
                    return

                data = await resp.json()
                answer = data.get("choices", [{}])[0].get("message", {}).get("content", "Я не понял ле, повтори")
                await message.reply(answer)

                history.append({"role": "assistant", "content": answer})
                history[:] = history[-MAX_HISTORY:]

        except Exception as e:
            await message.reply(f"Ошибка: {str(e)}")


# ===== Обработка фото =====
@dp.message(F.photo)
async def handle_photo(message: types.Message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    user_name = message.from_user.first_name

    async with get_lock(chat_id, user_id):
        photo = message.photo[-1]
        file = await bot.get_file(photo.file_id)
        file_url = f"https://api.telegram.org/file/bot{API_TOKEN}/{file.file_path}"

        history = get_history(chat_id, user_id)
        history.append({
            "role": "user",
            "content": [
                {"type": "text", "text": f"{user_name} прислал фото:"},
                {"type": "image_url", "image_url": {"url": file_url}}
            ]
        })
        history[:] = history[-MAX_HISTORY:]

        payload = {
            "model": "openrouter/sonoma-sky-alpha",
            "messages": history
        }

        try:
            async with session.post(
                OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json"
                },
                json=payload
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    await message.reply(f"Ошибка API: {resp.status}\n{text}")
                    return

                data = await resp.json()
                answer = data.get("choices", [{}])[0].get("message", {}).get("content", "Нет ответа от ИИ.")
                await message.reply(answer)

                history.append({"role": "assistant", "content": answer})
                history[:] = history[-MAX_HISTORY:]

        except Exception as e:
            await message.reply(f"Ошибка: {str(e)}")


# ===== Запуск =====
async def on_startup(bot):
    global session
    session = aiohttp.ClientSession()
    print("Бот запущен...")


async def on_shutdown(bot):
    await session.close()
    print("Бот остановлен.")


async def main():
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())