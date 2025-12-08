import asyncpg
from typing import Optional, List, Tuple
import logging
from config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASS, MAX_HISTORY, ACCESS_DAYS
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

async def create_pool() -> asyncpg.Pool:
    """Создает пул соединений с PostgreSQL."""
    return await asyncpg.create_pool(
        host=DB_HOST, port=DB_PORT, database=DB_NAME,
        user=DB_USER, password=DB_PASS
    )

async def init_db(pool: asyncpg.Pool, reset: bool = False):
    """Создает таблицы безопасно: сначала базовые, потом FK через ALTER.
    Если reset=True — дропаем всё и пересоздаём (для dev)."""
    async with pool.acquire() as conn:
        if reset:
            # Для dev: дропаем всё, чтоб чистый старт
            await conn.execute("DROP TABLE IF EXISTS history CASCADE;")
            await conn.execute("DROP TABLE IF EXISTS payments CASCADE;")
            await conn.execute("DROP TABLE IF EXISTS users CASCADE;")
            await conn.execute("DROP TABLE IF EXISTS groups CASCADE;")
            logger.info("DB reset: таблицы дропнуты.")

        try:
            # 1. Создаём users (личный доступ для ЛС)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    username VARCHAR(255),
                    paid_until TIMESTAMP,  -- NULL = не оплачено (для ЛС)
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # 2. Создаём groups (групповой доступ)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS groups (
                    chat_id BIGINT PRIMARY KEY,
                    paid_until TIMESTAMP,  -- NULL = не оплачено
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # 3. Создаём payments (без REFERENCES сначала)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS payments (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    chat_id BIGINT,  -- Новый: для групп
                    amount INTEGER,
                    status VARCHAR(50) DEFAULT 'pending',
                    payload VARCHAR(255),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # 4. Создаём history (без REFERENCES)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS history (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    role VARCHAR(10),
                    content TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # 5. Добавляем FK для payments.user_id
            constraint_exists = await conn.fetchval("""
                SELECT COUNT(*) FROM information_schema.table_constraints 
                WHERE table_name = 'payments' AND constraint_type = 'FOREIGN KEY' AND constraint_name = 'fk_payments_user';
            """)
            if constraint_exists == 0:
                await conn.execute("""
                    ALTER TABLE payments 
                    ADD CONSTRAINT fk_payments_user 
                    FOREIGN KEY (user_id) REFERENCES users(user_id);
                """)
                logger.info("Added FK for payments.user_id.")

            # 6. Добавляем FK для history.user_id (остаётся личным)
            constraint_exists = await conn.fetchval("""
                SELECT COUNT(*) FROM information_schema.table_constraints 
                WHERE table_name = 'history' AND constraint_type = 'FOREIGN KEY' AND constraint_name = 'fk_history_user';
            """)
            if constraint_exists == 0:
                await conn.execute("""
                    ALTER TABLE history 
                    ADD CONSTRAINT fk_history_user 
                    FOREIGN KEY (user_id) REFERENCES users(user_id);
                """)
                logger.info("Added FK for history.")

            logger.info("DB init: таблицы готовы с groups.")
        except Exception as e:
            logger.error(f"DB init error: {e}. Проверь структуру: \\d groups в psql.")
            raise

async def register_user(pool: asyncpg.Pool, user_id: int, username: str) -> None:
    """Регистрирует пользователя в БД, если не существует."""
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (user_id, username, paid_until) VALUES ($1, $2, NULL) ON CONFLICT (user_id) DO NOTHING",
            user_id, username
        )

async def get_remaining_days(pool: asyncpg.Pool, user_id: int) -> int:
    """Для инфы: дней осталось для юзера (ЛС). -1 если истёк."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT paid_until FROM users WHERE user_id = $1", user_id
        )
        if not row or not row['paid_until']:
            return -1
        remaining = (row['paid_until'] - datetime.now()).days
        return max(0, remaining) if remaining > 0 else 0

async def is_user_paid(pool: asyncpg.Pool, user_id: int) -> bool:
    """Проверяет личный доступ (для ЛС): paid_until > NOW()."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT paid_until FROM users WHERE user_id = $1", user_id
        )
        if not row or not row['paid_until']:
            return False
        return row['paid_until'] > datetime.now()

async def set_user_paid(pool: asyncpg.Pool, user_id: int) -> None:
    """Устанавливает личный paid_until = NOW() + ACCESS_DAYS."""
    expiration = datetime.now() + timedelta(days=ACCESS_DAYS)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET paid_until = $1 WHERE user_id = $2",
            expiration, user_id
        )

async def is_group_paid(pool: asyncpg.Pool, chat_id: int) -> bool:
    """Проверяет групповой доступ: paid_until > NOW()."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT paid_until FROM groups WHERE chat_id = $1", chat_id
        )
        if not row or not row['paid_until']:
            return False
        return row['paid_until'] > datetime.now()

async def set_group_paid(pool: asyncpg.Pool, chat_id: int) -> None:
    """Устанавливает групповой paid_until = NOW() + ACCESS_DAYS."""
    expiration = datetime.now() + timedelta(days=ACCESS_DAYS)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE groups SET paid_until = $1 WHERE chat_id = $2",
            expiration, chat_id
        )
        # Если не существует — вставляем
        await conn.execute(
            "INSERT INTO groups (chat_id, paid_until) VALUES ($1, $2) ON CONFLICT (chat_id) DO NOTHING",
            chat_id, expiration
        )

async def get_group_remaining_days(pool: asyncpg.Pool, chat_id: int) -> int:
    """Для инфы: дней осталось в группе. -1 если истёк."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT paid_until FROM groups WHERE chat_id = $1", chat_id
        )
        if not row or not row['paid_until']:
            return -1
        remaining = (row['paid_until'] - datetime.now()).days
        return max(0, remaining) if remaining > 0 else 0

async def save_payment(pool: asyncpg.Pool, user_id: int, chat_id: Optional[int], amount: int, payload: str) -> int:
    """Сохраняет платеж в pending (теперь с chat_id)."""
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO payments (user_id, chat_id, amount, payload) VALUES ($1, $2, $3, $4) RETURNING id",
            user_id, chat_id, amount, payload
        )

async def update_payment_status(pool: asyncpg.Pool, payment_id: int, status: str) -> None:
    """Обновляет статус платежа."""
    async with pool.acquire() as conn:
        await conn.execute("UPDATE payments SET status = $1 WHERE id = $2", status, payment_id)

async def get_history(pool: asyncpg.Pool, user_id: int) -> List[Tuple[str, str]]:
    """Возвращает последние MAX_HISTORY сообщений: [(role, content), ...]."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT role, content FROM history 
            WHERE user_id = $1 
            ORDER BY created_at DESC 
            LIMIT $2
            """,
            user_id, MAX_HISTORY
        )
        # Реверс для хронологического порядка
        return list(reversed([(row['role'], row['content']) for row in rows]))

async def save_to_history(pool: asyncpg.Pool, user_id: int, role: str, content: str) -> None:
    """Сохраняет сообщение в историю. Удаляет старые, если > MAX_HISTORY."""
    async with pool.acquire() as conn:
        # Сохраняем новое
        await conn.execute(
            "INSERT INTO history (user_id, role, content) VALUES ($1, $2, $3)",
            user_id, role, content
        )
        # Удаляем старые
        await conn.execute(
            """
            DELETE FROM history 
            WHERE user_id = $1 AND id NOT IN (
                SELECT id FROM history WHERE user_id = $1 ORDER BY created_at DESC LIMIT $2
            )
            """,
            user_id, MAX_HISTORY
        )

async def check_tables(pool: asyncpg.Pool) -> dict:
    """Проверяет, существуют ли таблицы. Возвращает {'users': True/False, ...}."""
    async with pool.acquire() as conn:
        tables = ['users', 'payments', 'history', 'groups']
        exists = {}
        for table in tables:
            count = await conn.fetchval(
                f"SELECT COUNT(*) FROM information_schema.tables WHERE table_name = '{table}'"
            )
            exists[table] = count > 0
        return exists