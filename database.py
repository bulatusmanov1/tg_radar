import sqlite3
from datetime import datetime

import config

PLACE_SQL = "COALESCE(topic, chat_name)"
DAY_SQL = "substr(date, 1, 10)"
IMPORTANT_SQL = "SUM(score >= ? AND is_mine = 0)"


class Telegram_DB:
    def __init__(self, db_path):
        """
        Инициализация соединения с SQLite 
        """
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.create_function("py_lower", 1, lambda s: s.lower() if s else s)
        self.cursor = self.conn.cursor()

    def init_db_messages(self):
        """
        Создание таблицы messages
        topic — тема форума (NULL, если в чате нет тем)
        is_admin — отправитель совершал админские действия (создавал темы, закреплял)
        score = NULL — сообщение ещё не оценено; reasons — сработавшие фильтры через "; "
        is_question — вопрос ко мне, answered — я писал в эту тему/чат после вопроса
        """
        self.cursor.execute('DROP TABLE IF EXISTS messages;')

        self.cursor.execute('''
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                chat_name TEXT,
                chat_type TEXT,
                topic TEXT,
                message_id INTEGER,
                sender TEXT,
                sender_id TEXT,
                date TEXT,
                text TEXT,
                reply_to INTEGER,
                media TEXT,
                is_mine INTEGER DEFAULT 0,
                is_admin INTEGER DEFAULT 0,
                is_pinned INTEGER DEFAULT 0,
                score INTEGER,
                reasons TEXT,
                is_question INTEGER DEFAULT 0,
                answered INTEGER DEFAULT 0,
                UNIQUE (chat_id, message_id)
            );
        ''')
        self.conn.commit()

    def init_db_deadlines(self):
        """
        Создание таблицы deadlines
        """
        self.cursor.execute('DROP TABLE IF EXISTS deadlines;')

        self.cursor.execute('''
            CREATE TABLE deadlines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_ref INTEGER REFERENCES messages(id),
                date TEXT,
                kind TEXT,
                raw TEXT,
                is_done INTEGER DEFAULT 0,
                UNIQUE (message_ref, date)
            );
        ''')
        self.conn.commit()

    def init_all(self):
        """
        Создание всех таблиц с нуля (старые данные удаляются)
        """
        self.init_db_messages()
        self.init_db_deadlines()

    def tables_exist(self) -> bool:
        """
        Проверка, что таблицы уже созданы
        """
        self.cursor.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name IN ('messages', 'deadlines');")
        return self.cursor.fetchone()[0] == 2

    def commit(self):
        """
        Сохранение изменений.
        """
        self.conn.commit()

    def insert_messages(self, messages: list[dict]) -> int:
        """
        Вставка сообщений из экспорта. Уже существующие (тот же чат и id) пропускаются.
        Возвращает:
            сколько сообщений добавлено
        """
        before = self.conn.total_changes
        self.cursor.executemany('''
            INSERT OR IGNORE INTO messages (chat_id, chat_name, chat_type, topic, message_id, sender, sender_id,
                                            date, text, reply_to, media, is_mine, is_admin, is_pinned)
            VALUES (:chat_id, :chat_name, :chat_type, :topic, :message_id, :sender, :sender_id,
                    :date, :text, :reply_to, :media, :is_mine, :is_admin, :is_pinned);
        ''', messages)
        self.conn.commit()
        return self.conn.total_changes - before

    def mark_admins(self, chat_id: int, admin_ids: set):
        """
        Пометить сообщения админов
        """
        for admin_id in admin_ids:
            self.cursor.execute('UPDATE messages SET is_admin = 1 WHERE chat_id = ? AND sender_id = ?;', (chat_id, admin_id))
        self.conn.commit()

    def get_messages_for_scoring(self, only_new: bool = True) -> list[dict]:
        """
        Сообщения для оценки: только новые (score IS NULL) или все (для rescore)
        Дата переводится из строки в datetime
        """
        condition = "WHERE score IS NULL" if only_new else ""
        self.cursor.execute(f'SELECT * FROM messages {condition} ORDER BY date;')
        messages = [dict(row) for row in self.cursor.fetchall()]
        for msg in messages:
            msg["date"] = datetime.strptime(msg["date"], config.DATETIME_FORMAT)
        return messages

    def get_my_message_keys(self) -> set:
        """
        Множество (chat_id, message_id) моих сообщений — для фильтра "ответ мне"
        """
        self.cursor.execute('SELECT chat_id, message_id FROM messages WHERE is_mine = 1;')
        return {(row[0], row[1]) for row in self.cursor.fetchall()}

    def save_score(self, message_ref: int, score: int, reasons: list, is_question: bool):
        """
        Сохранение оценки сообщения
        """
        self.cursor.execute(
            'UPDATE messages SET score = ?, reasons = ?, is_question = ? WHERE id = ?;',
            (score, "; ".join(reasons), int(is_question), message_ref)
        )

    def insert_deadlines(self, message_ref: int, deadlines: list[dict]):
        """
        Сохранение дат, найденных в сообщении (без commit)
        """
        for item in deadlines:
            self.cursor.execute('''
                INSERT OR IGNORE INTO deadlines (message_ref, date, kind, raw)
                VALUES (?, ?, ?, ?);
            ''', (message_ref, item["date"].strftime(config.DEADLINE_FORMAT), item["kind"], item["raw"]))

    def update_answered(self):
        """
        Вопрос ко мне считается отвеченным, если позже я писал в тот же чат и ту же тему
        Один запрос на всю таблицу: для каждого вопроса проверяется, есть ли моё сообщение после него
        """
        self.cursor.execute('''
            UPDATE messages
            SET answered = EXISTS (
                SELECT 1 FROM messages AS mine
                WHERE mine.is_mine = 1
                  AND mine.chat_id = messages.chat_id
                  AND mine.topic IS messages.topic
                  AND mine.date > messages.date
            )
            WHERE is_question = 1;
        ''')
        self.conn.commit()

    def get_last_date(self):
        """
        Дата последнего сообщения в базе ("ГГГГ-ММ-ДД") или None
        """
        self.cursor.execute('SELECT substr(MAX(date), 1, 10) FROM messages;')
        return self.cursor.fetchone()[0]

    def get_totals(self):
        """
        Общие цифры: (сообщений, чатов, первая дата, последняя дата)
        """
        self.cursor.execute('SELECT COUNT(*), COUNT(DISTINCT chat_id), MIN(date), MAX(date) FROM messages;')
        return self.cursor.fetchone()

    def get_day_overview(self, day: str, min_score: int):
        """
        Сводка за день по местам (тема или чат): (место, всего сообщений, важных)
        """
        self.cursor.execute(f'''
            SELECT {PLACE_SQL} AS place, COUNT(*) AS total, {IMPORTANT_SQL}
            FROM messages
            WHERE {DAY_SQL} = ?
            GROUP BY place
            ORDER BY total DESC;
        ''', (min_score, day))
        return self.cursor.fetchall()

    def get_important(self, min_score: int, date_from: str, date_to: str, limit: int = config.LIST_LIMIT):
        """
        Важные сообщения за период [date_from, date_to] (даты "ГГГГ-ММ-ДД"), сначала самые важные
        """
        self.cursor.execute(f'''
            SELECT id, date, {PLACE_SQL}, sender, text, score, reasons
            FROM messages
            WHERE is_mine = 0 AND score >= ? AND {DAY_SQL} BETWEEN ? AND ?
            ORDER BY score DESC, date DESC
            LIMIT ?;
        ''', (min_score, date_from, date_to, limit))
        return self.cursor.fetchall()

    def get_message(self, message_ref: int):
        """
        Одно сообщение целиком
        """
        self.cursor.execute('''
            SELECT id, date, chat_name, topic, sender, text, score, reasons
            FROM messages WHERE id = ?;
        ''', (message_ref,))
        return self.cursor.fetchone()

    def get_message_deadlines(self, message_ref: int):
        """
        Даты, найденные в одном сообщении
        """
        self.cursor.execute('SELECT date, kind, raw FROM deadlines WHERE message_ref = ? ORDER BY date;', (message_ref,))
        return self.cursor.fetchall()

    def get_deadlines(self, date_from: str = None, created_on: str = None, min_score: int = None):
        """
        Невыполненные дедлайны: (id, дата, вид, место, текст)
        date_from — только дедлайны не раньше этой даты ("ГГГГ-ММ-ДД ЧЧ:ММ")
        created_on — только из сообщений этого дня ("ГГГГ-ММ-ДД"), для сводки day
        min_score — только из сообщений, набравших столько баллов (отсекает даты из болтовни)
        """
        conditions, params = ["d.is_done = 0"], []
        if min_score is not None:
            conditions.append("m.score >= ?")
            params.append(min_score)
        if date_from:
            conditions.append("d.date >= ?")
            params.append(date_from)
        if created_on:
            conditions.append("substr(m.date, 1, 10) = ?")
            params.append(created_on)
        self.cursor.execute(f'''
            SELECT d.id, d.date, d.kind, {PLACE_SQL}, m.text
            FROM deadlines d
            JOIN messages m ON m.id = d.message_ref
            WHERE {" AND ".join(conditions)}
            ORDER BY d.date;
        ''', params)
        return self.cursor.fetchall()

    def mark_deadline_done(self, deadline_id: int) -> bool:
        """
        Отметить дедлайн выполненным
        """
        self.cursor.execute('UPDATE deadlines SET is_done = 1 WHERE id = ?;', (deadline_id,))
        self.conn.commit()
        return self.cursor.rowcount > 0

    def get_unanswered(self, day: str = None, limit: int = config.LIST_LIMIT):
        """
        Вопросы ко мне без ответа (за один день или за всё время)
        """
        day_condition = f"AND {DAY_SQL} = ?" if day else ""
        params = (day, limit) if day else (limit,)
        self.cursor.execute(f'''
            SELECT id, date, {PLACE_SQL}, sender, text, score
            FROM messages
            WHERE is_question = 1 AND answered = 0 {day_condition}
            ORDER BY date DESC
            LIMIT ?;
        ''', params)
        return self.cursor.fetchall()

    def search(self, text: str, limit: int = config.LIST_LIMIT):
        """
        Поиск по тексту без учёта регистра
        """
        self.cursor.execute(f'''
            SELECT id, date, {PLACE_SQL}, sender, text, score
            FROM messages
            WHERE py_lower(text) LIKE ?
            ORDER BY date DESC
            LIMIT ?;
        ''', (f"%{text.lower()}%", limit))
        return self.cursor.fetchall()

    def get_senders(self, min_score: int, limit: int = config.LIST_LIMIT):
        """
        Самые активные участники: (имя, id, сообщений, важных, админ ли)
        """
        self.cursor.execute('''
            SELECT sender, sender_id, COUNT(*) AS total, SUM(score >= ?), MAX(is_admin)
            FROM messages
            WHERE is_mine = 0
            GROUP BY sender_id
            ORDER BY total DESC
            LIMIT ?;
        ''', (min_score, limit))
        return self.cursor.fetchall()

    def get_activity_by_day(self, min_score: int, last_day: str, days: int):
        """
        Сообщений по дням за days дней до last_day: (день, всего, важных)
        """
        self.cursor.execute(f'''
            SELECT {DAY_SQL} AS day, COUNT(*), {IMPORTANT_SQL}
            FROM messages
            WHERE day > date(?, ?)
            GROUP BY day
            ORDER BY day;
        ''', (min_score, last_day, f"-{days} days"))
        return self.cursor.fetchall()

    def get_top_places(self, min_score: int, limit: int = config.TOP_LIMIT):
        """
        Самые активные темы/чаты: (место, всего, важных)
        """
        self.cursor.execute(f'''
            SELECT {PLACE_SQL} AS place, COUNT(*) AS total, {IMPORTANT_SQL}
            FROM messages
            GROUP BY place
            ORDER BY total DESC
            LIMIT ?;
        ''', (min_score, limit))
        return self.cursor.fetchall()

    def get_activity_by_hour(self):
        """
        Сообщений по часам суток: (час, количество)
        """
        self.cursor.execute('''
            SELECT CAST(substr(date, 12, 2) AS INTEGER) AS hour, COUNT(*)
            FROM messages
            GROUP BY hour
            ORDER BY hour;
        ''')
        return self.cursor.fetchall()

    def close(self):
        """
        Закрытие соединения
        """
        self.cursor.close()
        self.conn.close()
