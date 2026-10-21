"""
Настройки проекта TG Radar.

Здесь всё, что меняется под конкретного человека:
- подключение к PostgreSQL
- данные Telegram API (нужны только для режимов live и sync)
- кто такой "я" (username, имя, id) — от этого зависят упоминания и ответы мне
- веса фильтров важности (вес 0 = фильтр выключен)
- ключевые слова, VIP-отправители, пороги уровней
"""

DB_SETTINGS = {
    "dbname": "tg_radar",
    "user": "postgres",
    "password": "postgres",
    "host": "localhost",
    "port": 5432,
}

"""
Telegram API: получить api_id и api_hash можно на https://my.telegram.org -> API development tools.
Нужны только для команд live / sync. Для работы с экспортом (result.json) не нужны.
При первом запуске Telethon сам спросит номер телефона и код в терминале.
"""
TG_API_ID = 0
TG_API_HASH = ""
TG_SESSION_NAME = "tg_radar_session"
TG_PHONE = ""

"""
Опциональный бот для уведомлений (токен от @BotFather, chat_id — ваш id).
Если пусто — уведомления пишутся только в терминал и (в live-режиме) в «Избранное».
"""
BOT_TOKEN = ""
BOT_CHAT_ID = ""
NOTIFY_SAVED_MESSAGES = True
NOTIFY_MIN_LEVEL = "high"

"""
Кто я. MY_USER_ID подтягивается из полного экспорта автоматически,
но если экспортируете один чат — впишите его руками (узнать можно у @userinfobot).
MY_NAMES — корни имени, по ним ищется обращение по имени в тексте (Булат, Булата, Булату...).
"""
MY_USER_ID = None
MY_USERNAME = "@your_username"
MY_NAMES = ["булат", "bulat", "усманов"]

EXPORT_PATH = "export/result.json"
SKIP_CHAT_TYPES = []
STATS_DIR = "stats"

LEVELS = {
    "critical": 9,
    "high": 6,
    "medium": 3,
}

FILTER_WEIGHTS = {
    "mention_me": 4,
    "name_in_text": 3,
    "reply_to_me": 4,
    "personal_chat": 2,
    "question_to_me": 2,
    "unanswered_question": 2,
    "vip_sender": 3,
    "priority_chat": 1,
    "keywords": 1,
    "deadline": 3,
    "deadline_soon": 2,
    "urgency_marks": 1,
    "caps_lock": 1,
    "mass_mention": 2,
    "pinned": 3,
    "announcement": 2,
    "important_links": 1,
    "document_file": 1,
    "schedule_change": 2,
    "meeting_time": 1,
    "money": 1,
    "popular_reactions": 1,
    "hot_discussion": 1,
    "long_message": 1,
    "noise_short": -3,
    "media_without_text": -2,
    "emoji_only": -2,
    "forwarded": -1,
    "bot_message": -1,
    "muted_chat": -4,
    "old_message": 0,
}

KEYWORDS = {
    r"\bсрочн": 3,
    r"\basap\b": 3,
    r"\bважн": 2,
    r"\bдедлайн": 3,
    r"\bdeadline": 3,
    r"\bэкзамен": 3,
    r"\bпересдач": 3,
    r"\bзач[её]т": 2,
    r"\bконтрольн": 2,
    r"\bколлоквиум": 2,
    r"\bсдать\b|\bсдач": 2,
    r"\bдомашк|\bдз\b": 2,
    r"\bведомост": 2,
    r"\bоценк": 1,
    r"\bбалл": 1,
    r"\bрасписани": 1,
    r"\bобязательн": 2,
    r"\bнапомина": 1,
    r"\bпроверь|\bпосмотри": 1,
}

VIP_SENDERS = []
VIP_CHATS = []

NOISE_WORDS = [
    "ок", "ok", "окей", "+", "++", "спс", "спасибо", "пасиб", "ага", "угу", "да", "нет",
    "лол", "кек", "ясно", "понял", "поняла", "норм", "го", "ахах", "хаха", "))", ")", "👍", "👌",
]

POPULAR_REACTIONS_MIN = 5
HOT_DISCUSSION_MIN_REPLIES = 5
LONG_MESSAGE_MIN_CHARS = 350
OLD_MESSAGE_DAYS = 30
DEADLINE_SOON_DAYS = 3
ANALYZE_BATCH_SIZE = 2000
