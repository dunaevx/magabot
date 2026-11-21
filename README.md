# MagaBot

Telegram-бот «Мага» — собеседник с характером. Отвечает через модели OpenRouter.

## Как с ним говорить

- в группе: начните сообщение с «Мага, …» или ответьте на его сообщение;
- в личке: просто пишите;
- `/reset` — Мага забудет ваш разговор.

## Запуск

```bash
docker build -t magabot .
docker run -d --restart unless-stopped \
  -e API_TOKEN=<токен от @BotFather> \
  -e OPENROUTER_API_KEY=<ключ OpenRouter> \
  magabot
```

## Настройки (переменные окружения)

| Переменная | По умолчанию | Что делает |
|---|---|---|
| `API_TOKEN` | — | токен бота (обязательно) |
| `OPENROUTER_API_KEY` | — | ключ OpenRouter (обязательно) |
| `MODEL` | `meituan/longcat-flash-chat:free` | модель на OpenRouter |
| `MAX_HISTORY` | 14 | сколько последних реплик помнить |
| `COOLDOWN_SECONDS` | 2 | минимальная пауза между вопросами одного человека |
| `REQUEST_TIMEOUT` | 60 | таймаут запроса к модели, сек |
| `SITE_URL` | ссылка на репозиторий | заголовок HTTP-Referer для OpenRouter |

Чтобы Мага видел в группе сообщения «Мага, …», у бота должен быть выключен privacy mode в @BotFather (`/setprivacy` → Disable) или бот должен быть админом группы. Ответы на его сообщения он получает в любом случае.
