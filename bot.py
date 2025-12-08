import asyncio
import logging
import os  # Для PATH
from aiogram import Bot, Dispatcher, BaseMiddleware
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp import ClientSession
import asyncpg
from config import BOT_TOKEN, RESET_DB
from db import create_pool, init_db, check_tables
from handlers.chat import router as chat_router
from handlers.pay import router as pay_router
from handlers.admin import router as admin_router  # Если есть
from handlers.voice import router as voice_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class PoolSessionMiddleware(BaseMiddleware):
    """Middleware: пихает pool и session в data для всех хендлеров."""
    def __init__(self, pool: asyncpg.Pool, session: ClientSession):
        self.pool = pool
        self.session = session

    async def __call__(self, handler, event, data):
        data['pool'] = self.pool
        data['session'] = self.session
        return await handler(event, data)

async def main():
    """Запуск бота: Инициализация pool, session, middleware, роутеры."""
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN не задан в .env")
    
    # Фикс для pydub: добавляем ffmpeg в PATH (твой путь, братан)
    ffmpeg_bin = r'C:\ffmpeg\bin'  # Измени, если не там
    if os.path.exists(ffmpeg_bin) and ffmpeg_bin not in os.environ['PATH']:
        os.environ['PATH'] = os.environ['PATH'] + os.pathsep + ffmpeg_bin
        logger.info(f"PATH обновлён для ffmpeg: {ffmpeg_bin}")
    
    pool = await create_pool()
    await init_db(pool, reset=RESET_DB)
    status = await check_tables(pool)
    logger.info(f"Tables check: {status}")
    
    session = ClientSession()  # aiohttp сессия для API
    
    # Dispatcher с FSM storage
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    
    # Middleware для pool/session
    middleware = PoolSessionMiddleware(pool, session)
    dp.update.outer_middleware(middleware)  # Глобально для всех роутеров
    
    # Роутеры
    dp.include_router(chat_router)
    dp.include_router(pay_router)
    dp.include_router(admin_router)  # Если подключаешь
    dp.include_router(voice_router)
    
    try:
        bot = Bot(token=BOT_TOKEN)
        await dp.start_polling(bot)
    finally:
        await pool.close()
        await session.close()

if __name__ == "__main__":
    asyncio.run(main())