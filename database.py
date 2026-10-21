import psycopg2
from psycopg2.extras import execute_values


class Telegram_DB:
    def __init__(self, dbname, user, password, host, port):
        """
        Инициализация соединения с PostgreSQL
        """
        self.conn = psycopg2.connect(
            dbname=dbname,
            user=user,
            password=password,
            host=host,
            port=port
        )
        self.conn.autocommit = True
        self.cursor = self.conn.cursor()

    def init_db_chats(self):
        """
        Создание таблицы chats
        priority — ручной приоритет чата (добавляется к важности сообщений)
        is_muted — "заглушённый" чат, сообщения из него штрафуются
        """
        self.cursor.execute('DROP TABLE IF EXISTS chats CASCADE;')

        self.cursor.execute('''
            CREATE TABLE chats (
                id SERIAL PRIMARY KEY,
                chat_id BIGINT UNIQUE,
                chat_name TEXT,
                chat_type TEXT,
                priority INT DEFAULT 0,
                is_muted BOOLEAN DEFAULT FALSE
            );
        ''')

    def init_db_messages(self):
        """
        Создание таблицы messages
        Пара (chat_id, message_id) уникальна — повторный импорт не создаёт дублей
        """
        self.cursor.execute('DROP TABLE IF EXISTS messages CASCADE;')

        self.cursor.execute('''
            CREATE TABLE messages (
                id SERIAL PRIMARY KEY,
                chat_id BIGINT,
                message_id BIGINT,
                sender_name TEXT,
                sender_id TEXT,
                msg_date TIMESTAMP,
                msg_text TEXT,
                reply_to BIGINT,
                is_mine BOOLEAN DEFAULT FALSE,
                is_pinned BOOLEAN DEFAULT FALSE,
                media_type TEXT,
                file_name TEXT,
                forwarded_from TEXT,
                via_bot TEXT,
                mentions TEXT[],
                links TEXT[],
                reactions_count INT DEFAULT 0,
                UNIQUE (chat_id, message_id)
            );
        ''')
        self.cursor.execute('CREATE INDEX idx_messages_date ON messages (msg_date);')
        self.cursor.execute('CREATE INDEX idx_messages_chat ON messages (chat_id, msg_date);')

    def init_db_analysis(self):
        """
        Создание таблицы analysis
        Результат оценки важности: баллы, уровень и причины (какие фильтры сработали)
        """
        self.cursor.execute('DROP TABLE IF EXISTS analysis CASCADE;')

        self.cursor.execute('''
            CREATE TABLE analysis (
                id SERIAL PRIMARY KEY,
                message_ref INT UNIQUE REFERENCES messages(id) ON DELETE CASCADE,
                score INT,
                level TEXT,
                reasons TEXT[],
                is_read BOOLEAN DEFAULT FALSE,
                analyzed_at TIMESTAMP DEFAULT NOW()
            );
        ''')
        self.cursor.execute('CREATE INDEX idx_analysis_level ON analysis (level, is_read);')

    def init_db_deadlines(self):
        """
        Создание таблицы deadlines
        kind = 'deadline' (есть слова "сдать", "до", "дедлайн"...) или 'event' (просто дата)
        """
        self.cursor.execute('DROP TABLE IF EXISTS deadlines CASCADE;')

        self.cursor.execute('''
            CREATE TABLE deadlines (
                id SERIAL PRIMARY KEY,
                message_ref INT REFERENCES messages(id) ON DELETE CASCADE,
                deadline_date TIMESTAMP,
                kind TEXT,
                raw_text TEXT,
                is_done BOOLEAN DEFAULT FALSE,
                UNIQUE (message_ref, deadline_date)
            );
        ''')

    def init_db_rules(self):
        """
        Создание таблицы rules — пользовательские правила поверх config.py
        rule_type: 'keyword' (регулярка), 'sender' (имя/id отправителя), 'chat' (название чата)
        """
        self.cursor.execute('DROP TABLE IF EXISTS rules CASCADE;')

        self.cursor.execute('''
            CREATE TABLE rules (
                id SERIAL PRIMARY KEY,
                rule_type TEXT,
                rule_value TEXT,
                weight INT DEFAULT 1,
                UNIQUE (rule_type, rule_value)
            );
        ''')

    def init_all(self):
        """
        Создание всех таблиц с нуля (старые данные удаляются)
        """
        self.init_db_chats()
        self.init_db_messages()
        self.init_db_analysis()
        self.init_db_deadlines()
        self.init_db_rules()

    def tables_exist(self) -> bool:
        """
        Проверка, что таблицы уже созданы
        """
        self.cursor.execute("SELECT to_regclass('public.messages') IS NOT NULL;")
        return bool(self.cursor.fetchone()[0])

    def insert_chat(self, chat_id, chat_name, chat_type):
        """
        Вставка/обновление чата (название могло поменяться)
        """
        self.cursor.execute('''
            INSERT INTO chats (chat_id, chat_name, chat_type)
            VALUES (%s, %s, %s)
            ON CONFLICT (chat_id) DO UPDATE SET
                chat_name = EXCLUDED.chat_name,
                chat_type = EXCLUDED.chat_type;
        ''', (chat_id, chat_name, chat_type))

    def insert_messages(self, messages: list[dict]) -> int:
        """
        Пакетная вставка сообщений
        Параметры:
            messages: список словарей (формат см. в tg_import.py)
        Возвращает:
            int: сколько сообщений реально добавлено (дубли пропускаются)
        """
        if not messages:
            return 0

        rows = [(
            m["chat_id"], m["message_id"], m["sender_name"], m["sender_id"], m["msg_date"],
            m["msg_text"], m["reply_to"], m["is_mine"], m["is_pinned"], m["media_type"],
            m["file_name"], m["forwarded_from"], m["via_bot"], m["mentions"], m["links"],
            m["reactions_count"]
        ) for m in messages]

        inserted = 0
        page = 1000
        for start in range(0, len(rows), page):
            chunk = rows[start:start + page]
            returned = execute_values(self.cursor, '''
                INSERT INTO messages (chat_id, message_id, sender_name, sender_id, msg_date,
                                      msg_text, reply_to, is_mine, is_pinned, media_type,
                                      file_name, forwarded_from, via_bot, mentions, links,
                                      reactions_count)
                VALUES %s
                ON CONFLICT (chat_id, message_id) DO UPDATE SET
                    is_pinned = messages.is_pinned OR EXCLUDED.is_pinned,
                    reactions_count = GREATEST(messages.reactions_count, EXCLUDED.reactions_count)
                RETURNING (xmax = 0);
            ''', chunk, page_size=page, fetch=True)
            inserted += sum(1 for row in returned if row[0])
        return inserted

    def set_pinned(self, chat_id, message_ids: list[int]):
        """
        Пометка сообщений как закреплённых
        """
        if message_ids:
            self.cursor.execute(
                "UPDATE messages SET is_pinned = TRUE WHERE chat_id = %s AND message_id = ANY(%s);",
                (chat_id, list(message_ids))
            )

    def get_message_ref(self, chat_id, message_id):
        """
        Внутренний id (messages.id) по паре (chat_id, message_id)
        """
        self.cursor.execute(
            'SELECT id FROM messages WHERE chat_id = %s AND message_id = %s;',
            (chat_id, message_id)
        )
        row = self.cursor.fetchone()
        return row[0] if row else None

    def get_chats(self):
        """
        Список чатов со статистикой: всего сообщений, важных непрочитанных, последнее сообщение
        """
        self.cursor.execute('''
            SELECT c.chat_id, c.chat_name, c.chat_type, c.priority, c.is_muted,
                   COUNT(m.id) AS total,
                   COUNT(a.id) FILTER (WHERE a.level IN ('critical', 'high') AND NOT a.is_read) AS important,
                   MAX(m.msg_date) AS last_date
            FROM chats c
            LEFT JOIN messages m ON m.chat_id = c.chat_id
            LEFT JOIN analysis a ON a.message_ref = m.id
            GROUP BY c.chat_id, c.chat_name, c.chat_type, c.priority, c.is_muted
            ORDER BY important DESC, last_date DESC NULLS LAST;
        ''')
        return self.cursor.fetchall()

    def get_chat_info_map(self) -> dict:
        """
        Словарь chat_id -> {name, type, priority, is_muted} для фильтров
        """
        self.cursor.execute('SELECT chat_id, chat_name, chat_type, priority, is_muted FROM chats;')
        return {
            row[0]: {"name": row[1], "type": row[2], "priority": row[3], "is_muted": row[4]}
            for row in self.cursor.fetchall()
        }

    def find_chat(self, query: str):
        """
        Поиск чата по id или части названия
        Возвращает:
            list: подходящие чаты (chat_id, chat_name)
        """
        if query.lstrip("-").isdigit():
            self.cursor.execute('SELECT chat_id, chat_name FROM chats WHERE chat_id = %s;', (int(query),))
        else:
            self.cursor.execute(
                'SELECT chat_id, chat_name FROM chats WHERE chat_name ILIKE %s ORDER BY chat_name;',
                (f"%{query}%",)
            )
        return self.cursor.fetchall()

    def set_chat_muted(self, chat_id, muted: bool = True):
        """
        Заглушить / включить чат
        """
        self.cursor.execute('UPDATE chats SET is_muted = %s WHERE chat_id = %s;', (muted, chat_id))
        return self.cursor.rowcount > 0

    def set_chat_priority(self, chat_id, priority: int):
        """
        Установка приоритета чата (обычно от -5 до 5)
        """
        self.cursor.execute('UPDATE chats SET priority = %s WHERE chat_id = %s;', (priority, chat_id))
        return self.cursor.rowcount > 0

    def get_messages_for_analysis(self, only_new: bool = True, limit: int = None):
        """
        Получение сообщений для оценки важности
        Параметры:
            only_new: True — только ещё не оценённые
            limit: ограничение количества
        """
        query = '''
            SELECT m.id, m.chat_id, m.message_id, m.sender_name, m.sender_id, m.msg_date,
                   m.msg_text, m.reply_to, m.is_mine, m.is_pinned, m.media_type, m.file_name,
                   m.forwarded_from, m.via_bot, m.mentions, m.links, m.reactions_count
            FROM messages m
        '''
        if only_new:
            query += ' LEFT JOIN analysis a ON a.message_ref = m.id WHERE a.id IS NULL'
        query += ' ORDER BY m.msg_date'
        if limit:
            query += f' LIMIT {int(limit)}'

        self.cursor.execute(query + ';')
        columns = [
            "id", "chat_id", "message_id", "sender_name", "sender_id", "msg_date",
            "msg_text", "reply_to", "is_mine", "is_pinned", "media_type", "file_name",
            "forwarded_from", "via_bot", "mentions", "links", "reactions_count"
        ]
        return [dict(zip(columns, row)) for row in self.cursor.fetchall()]

    def get_my_message_keys(self) -> set:
        """
        Множество (chat_id, message_id) моих сообщений — чтобы понять, что мне ответили
        """
        self.cursor.execute('SELECT chat_id, message_id FROM messages WHERE is_mine;')
        return set(self.cursor.fetchall())

    def get_reply_counts(self) -> dict:
        """
        Сколько ответов получило каждое сообщение: (chat_id, message_id) -> count
        """
        self.cursor.execute('''
            SELECT chat_id, reply_to, COUNT(*)
            FROM messages
            WHERE reply_to IS NOT NULL
            GROUP BY chat_id, reply_to;
        ''')
        return {(row[0], row[1]): row[2] for row in self.cursor.fetchall()}

    def get_last_my_message_dates(self) -> dict:
        """
        Дата моего последнего сообщения в каждом чате: chat_id -> datetime
        """
        self.cursor.execute('SELECT chat_id, MAX(msg_date) FROM messages WHERE is_mine GROUP BY chat_id;')
        return dict(self.cursor.fetchall())

    def save_analysis(self, results: list[tuple]):
        """
        Сохранение результатов анализа
        Параметры:
            results: список (message_ref, score, level, reasons, is_read)
        """
        if not results:
            return
        execute_values(self.cursor, '''
            INSERT INTO analysis (message_ref, score, level, reasons, is_read)
            VALUES %s
            ON CONFLICT (message_ref) DO UPDATE SET
                score = EXCLUDED.score,
                level = EXCLUDED.level,
                reasons = EXCLUDED.reasons,
                analyzed_at = NOW();
        ''', results, page_size=1000)

    def save_deadlines(self, deadlines: list[tuple]):
        """
        Сохранение найденных дедлайнов
        Параметры:
            deadlines: список (message_ref, deadline_date, kind, raw_text)
        """
        if not deadlines:
            return
        execute_values(self.cursor, '''
            INSERT INTO deadlines (message_ref, deadline_date, kind, raw_text)
            VALUES %s
            ON CONFLICT (message_ref, deadline_date) DO NOTHING;
        ''', deadlines, page_size=1000)

    def clear_analysis(self):
        """
        Удаление результатов анализа (например, после смены весов в config.py)
        Отметки "прочитано" и "сделано" при этом теряются
        """
        self.cursor.execute('DELETE FROM analysis;')
        self.cursor.execute('DELETE FROM deadlines;')

    def get_inbox(self, levels=("critical", "high"), limit=20, include_read=False, days=None, chat_id=None):
        """
        "Входящие": важные сообщения, отсортированные по баллам
        Параметры:
            levels: какие уровни показывать
            limit: сколько строк
            include_read: показывать ли уже прочитанные
            days: только за последние N дней (считается от последнего сообщения в базе)
            chat_id: только из одного чата
        """
        conditions = ["a.level = ANY(%s)"]
        params = [list(levels)]
        if not include_read:
            conditions.append("NOT a.is_read")
        if days:
            conditions.append("m.msg_date >= (SELECT MAX(msg_date) FROM messages) - %s * INTERVAL '1 day'")
            params.append(days)
        if chat_id is not None:
            conditions.append("m.chat_id = %s")
            params.append(chat_id)
        params.append(limit)

        self.cursor.execute(f'''
            SELECT m.id, c.chat_name, m.sender_name, m.msg_date, m.msg_text,
                   a.score, a.level, a.reasons, a.is_read, m.media_type
            FROM analysis a
            JOIN messages m ON m.id = a.message_ref
            LEFT JOIN chats c ON c.chat_id = m.chat_id
            WHERE {" AND ".join(conditions)}
            ORDER BY a.score DESC, m.msg_date DESC
            LIMIT %s;
        ''', tuple(params))
        return self.cursor.fetchall()

    def mark_read(self, message_refs: list[int]) -> int:
        """
        Пометить сообщения прочитанными
        Возвращает:
            int: сколько строк обновлено
        """
        self.cursor.execute(
            'UPDATE analysis SET is_read = TRUE WHERE message_ref = ANY(%s);',
            (list(message_refs),)
        )
        return self.cursor.rowcount

    def mark_all_read(self, levels=None) -> int:
        """
        Пометить прочитанными все сообщения (или только указанных уровней)
        """
        if levels:
            self.cursor.execute(
                'UPDATE analysis SET is_read = TRUE WHERE NOT is_read AND level = ANY(%s);',
                (list(levels),)
            )
        else:
            self.cursor.execute('UPDATE analysis SET is_read = TRUE WHERE NOT is_read;')
        return self.cursor.rowcount

    def get_deadlines(self, days=None, include_done=False, include_past=False, kind=None):
        """
        Список дедлайнов/событий
        Параметры:
            days: горизонт в днях от текущего момента
            include_done: показывать выполненные
            include_past: показывать прошедшие
            kind: 'deadline' / 'event' / None (все)
        """
        conditions = ["TRUE"]
        params = []
        if not include_done:
            conditions.append("NOT d.is_done")
        if not include_past:
            conditions.append("d.deadline_date >= NOW()")
        if days:
            conditions.append("d.deadline_date <= NOW() + %s * INTERVAL '1 day'")
            params.append(days)
        if kind:
            conditions.append("d.kind = %s")
            params.append(kind)

        self.cursor.execute(f'''
            SELECT d.id, d.deadline_date, d.kind, d.raw_text, d.is_done,
                   c.chat_name, m.sender_name, m.msg_text, m.id
            FROM deadlines d
            JOIN messages m ON m.id = d.message_ref
            LEFT JOIN chats c ON c.chat_id = m.chat_id
            WHERE {" AND ".join(conditions)}
            ORDER BY d.deadline_date;
        ''', tuple(params))
        return self.cursor.fetchall()

    def mark_deadline_done(self, deadline_id: int, done: bool = True) -> bool:
        """
        Отметить дедлайн выполненным (или вернуть обратно)
        """
        self.cursor.execute('UPDATE deadlines SET is_done = %s WHERE id = %s;', (done, deadline_id))
        return self.cursor.rowcount > 0

    def get_unanswered(self, limit=30):
        """
        Вопросы ко мне без ответа: сработал фильтр unanswered_question
        и после вопроса я в этот чат ничего не писал
        """
        self.cursor.execute('''
            SELECT m.id, c.chat_name, m.sender_name, m.msg_date, m.msg_text, a.score
            FROM analysis a
            JOIN messages m ON m.id = a.message_ref
            LEFT JOIN chats c ON c.chat_id = m.chat_id
            WHERE EXISTS (SELECT 1 FROM unnest(a.reasons) r WHERE r LIKE 'вопрос без ответа%%')
              AND NOT EXISTS (
                  SELECT 1 FROM messages mine
                  WHERE mine.chat_id = m.chat_id AND mine.is_mine AND mine.msg_date > m.msg_date
              )
            ORDER BY m.msg_date DESC
            LIMIT %s;
        ''', (limit,))
        return self.cursor.fetchall()

    def get_mentions(self, limit=30):
        """
        Сообщения, где меня упомянули или мне ответили
        """
        self.cursor.execute('''
            SELECT m.id, c.chat_name, m.sender_name, m.msg_date, m.msg_text, a.score
            FROM analysis a
            JOIN messages m ON m.id = a.message_ref
            LEFT JOIN chats c ON c.chat_id = m.chat_id
            WHERE EXISTS (
                SELECT 1 FROM unnest(a.reasons) r
                WHERE r LIKE 'упоминание%%' OR r LIKE 'ответ на моё%%' OR r LIKE 'обращение по имени%%'
            )
            ORDER BY m.msg_date DESC
            LIMIT %s;
        ''', (limit,))
        return self.cursor.fetchall()

    def search_messages(self, text: str, chat_id=None, limit=30):
        """
        Поиск по тексту сообщений (без учёта регистра)
        """
        params = [f"%{text}%"]
        chat_condition = ""
        if chat_id is not None:
            chat_condition = "AND m.chat_id = %s"
            params.append(chat_id)
        params.append(limit)

        self.cursor.execute(f'''
            SELECT m.id, c.chat_name, m.sender_name, m.msg_date, m.msg_text,
                   COALESCE(a.score, 0), COALESCE(a.level, '-')
            FROM messages m
            LEFT JOIN chats c ON c.chat_id = m.chat_id
            LEFT JOIN analysis a ON a.message_ref = m.id
            WHERE m.msg_text ILIKE %s {chat_condition}
            ORDER BY m.msg_date DESC
            LIMIT %s;
        ''', tuple(params))
        return self.cursor.fetchall()

    def get_message_details(self, message_ref: int):
        """
        Полная информация о сообщении + результат анализа + дедлайны
        """
        self.cursor.execute('''
            SELECT m.id, c.chat_name, c.chat_type, m.sender_name, m.msg_date, m.msg_text,
                   m.media_type, m.file_name, m.mentions, m.links, m.reactions_count,
                   a.score, a.level, a.reasons, a.is_read
            FROM messages m
            LEFT JOIN chats c ON c.chat_id = m.chat_id
            LEFT JOIN analysis a ON a.message_ref = m.id
            WHERE m.id = %s;
        ''', (message_ref,))
        row = self.cursor.fetchone()
        if row is None:
            return None

        columns = [
            "id", "chat_name", "chat_type", "sender_name", "msg_date", "msg_text",
            "media_type", "file_name", "mentions", "links", "reactions_count",
            "score", "level", "reasons", "is_read"
        ]
        details = dict(zip(columns, row))
        self.cursor.execute(
            'SELECT deadline_date, kind, raw_text FROM deadlines WHERE message_ref = %s ORDER BY deadline_date;',
            (message_ref,)
        )
        details["deadlines"] = self.cursor.fetchall()
        return details

    def get_context(self, message_ref: int, around: int = 3):
        """
        Соседние сообщения в том же чате (контекст переписки)
        """
        self.cursor.execute('''
            WITH target AS (SELECT chat_id, msg_date FROM messages WHERE id = %s)
            SELECT * FROM (
                (SELECT m.id, m.sender_name, m.msg_date, m.msg_text FROM messages m, target t
                 WHERE m.chat_id = t.chat_id AND m.msg_date <= t.msg_date
                 ORDER BY m.msg_date DESC, m.message_id DESC LIMIT %s)
                UNION
                (SELECT m.id, m.sender_name, m.msg_date, m.msg_text FROM messages m, target t
                 WHERE m.chat_id = t.chat_id AND m.msg_date > t.msg_date
                 ORDER BY m.msg_date LIMIT %s)
            ) ctx
            ORDER BY msg_date, id;
        ''', (message_ref, around + 1, around))
        return self.cursor.fetchall()

    def get_totals(self) -> dict:
        """
        Общие цифры: чаты, сообщения, мои сообщения, оценённые, важные непрочитанные, дедлайны
        """
        self.cursor.execute('''
            SELECT
                (SELECT COUNT(*) FROM chats),
                (SELECT COUNT(*) FROM messages),
                (SELECT COUNT(*) FROM messages WHERE is_mine),
                (SELECT COUNT(*) FROM analysis),
                (SELECT COUNT(*) FROM analysis WHERE level = 'critical' AND NOT is_read),
                (SELECT COUNT(*) FROM analysis WHERE level = 'high' AND NOT is_read),
                (SELECT COUNT(*) FROM deadlines WHERE NOT is_done AND deadline_date >= NOW()),
                (SELECT MIN(msg_date) FROM messages),
                (SELECT MAX(msg_date) FROM messages);
        ''')
        row = self.cursor.fetchone()
        keys = ["chats", "messages", "mine", "analyzed", "critical", "high", "deadlines", "first", "last"]
        return dict(zip(keys, row))

    def get_activity_by_day(self, days: int = 30, chat_id=None):
        """
        Количество сообщений по дням (всего / моих / важных)
        """
        params = [days]
        chat_condition = ""
        if chat_id is not None:
            chat_condition = "AND m.chat_id = %s"
            params.append(chat_id)

        self.cursor.execute(f'''
            SELECT m.msg_date::date AS day,
                   COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE m.is_mine) AS mine,
                   COUNT(a.id) FILTER (WHERE a.level IN ('critical', 'high')) AS important
            FROM messages m
            LEFT JOIN analysis a ON a.message_ref = m.id
            WHERE m.msg_date >= (SELECT MAX(msg_date) FROM messages) - %s * INTERVAL '1 day'
              {chat_condition}
            GROUP BY day
            ORDER BY day;
        ''', tuple(params))
        return self.cursor.fetchall()

    def get_activity_by_hour(self, chat_id=None):
        """
        Распределение сообщений по часам суток
        """
        chat_condition = "WHERE chat_id = %s" if chat_id is not None else ""
        params = (chat_id,) if chat_id is not None else ()
        self.cursor.execute(f'''
            SELECT EXTRACT(HOUR FROM msg_date)::int AS hour, COUNT(*)
            FROM messages {chat_condition}
            GROUP BY hour ORDER BY hour;
        ''', params)
        return self.cursor.fetchall()

    def get_activity_by_weekday(self):
        """
        Распределение сообщений по дням недели (1 = понедельник)
        """
        self.cursor.execute('''
            SELECT EXTRACT(ISODOW FROM msg_date)::int AS dow, COUNT(*)
            FROM messages GROUP BY dow ORDER BY dow;
        ''')
        return self.cursor.fetchall()

    def get_top_chats(self, limit: int = 10, by: str = "total"):
        """
        Самые активные чаты
        Параметры:
            by: 'total' — по числу сообщений, 'important' — по числу важных
        """
        order = "important" if by == "important" else "total"
        self.cursor.execute(f'''
            SELECT c.chat_name,
                   COUNT(m.id) AS total,
                   COUNT(a.id) FILTER (WHERE a.level IN ('critical', 'high')) AS important
            FROM chats c
            JOIN messages m ON m.chat_id = c.chat_id
            LEFT JOIN analysis a ON a.message_ref = m.id
            GROUP BY c.chat_name
            ORDER BY {order} DESC
            LIMIT %s;
        ''', (limit,))
        return self.cursor.fetchall()

    def get_top_senders(self, limit: int = 10, chat_id=None):
        """
        Самые активные отправители (кроме меня)
        """
        params = []
        chat_condition = ""
        if chat_id is not None:
            chat_condition = "AND m.chat_id = %s"
            params.append(chat_id)
        params.append(limit)

        self.cursor.execute(f'''
            SELECT m.sender_name, COUNT(*) AS total,
                   COUNT(a.id) FILTER (WHERE a.level IN ('critical', 'high')) AS important
            FROM messages m
            LEFT JOIN analysis a ON a.message_ref = m.id
            WHERE NOT m.is_mine AND m.sender_name IS NOT NULL {chat_condition}
            GROUP BY m.sender_name
            ORDER BY total DESC
            LIMIT %s;
        ''', tuple(params))
        return self.cursor.fetchall()

    def get_level_distribution(self):
        """
        Сколько сообщений на каждом уровне важности
        """
        self.cursor.execute('''
            SELECT level, COUNT(*) FROM analysis
            GROUP BY level
            ORDER BY MIN(score) DESC;
        ''')
        return self.cursor.fetchall()

    def get_reason_stats(self, limit: int = 20):
        """
        Какие фильтры срабатывают чаще всего (полезно для настройки весов)
        """
        self.cursor.execute('''
            SELECT regexp_replace(split_part(r, ':', 1), '\\s*\\([+-]\\d+\\)$', '') AS reason, COUNT(*)
            FROM analysis, unnest(reasons) AS r
            GROUP BY reason
            ORDER BY COUNT(*) DESC
            LIMIT %s;
        ''', (limit,))
        return self.cursor.fetchall()

    def get_response_time_stats(self, limit: int = 10):
        """
        Моё среднее время ответа в личках (в минутах): время от сообщения собеседника
        до моего следующего сообщения в этом чате
        """
        self.cursor.execute('''
            WITH ordered AS (
                SELECT m.chat_id, m.msg_date, m.is_mine,
                       LAG(m.is_mine) OVER w AS prev_mine,
                       LAG(m.msg_date) OVER w AS prev_date
                FROM messages m
                JOIN chats c ON c.chat_id = m.chat_id AND c.chat_type = 'personal_chat'
                WINDOW w AS (PARTITION BY m.chat_id ORDER BY m.msg_date, m.message_id)
            )
            SELECT c.chat_name,
                   ROUND(AVG(EXTRACT(EPOCH FROM (o.msg_date - o.prev_date)) / 60)::numeric, 1) AS avg_min,
                   COUNT(*) AS answers
            FROM ordered o
            JOIN chats c ON c.chat_id = o.chat_id
            WHERE o.is_mine AND o.prev_mine = FALSE
            GROUP BY c.chat_name
            ORDER BY avg_min DESC
            LIMIT %s;
        ''', (limit,))
        return self.cursor.fetchall()

    def add_rule(self, rule_type: str, rule_value: str, weight: int = 1):
        """
        Добавление правила (keyword / sender / chat)
        """
        self.cursor.execute('''
            INSERT INTO rules (rule_type, rule_value, weight)
            VALUES (%s, %s, %s)
            ON CONFLICT (rule_type, rule_value) DO UPDATE SET weight = EXCLUDED.weight;
        ''', (rule_type, rule_value, weight))

    def remove_rule(self, rule_id: int) -> bool:
        """
        Удаление правила по id
        """
        self.cursor.execute('DELETE FROM rules WHERE id = %s;', (rule_id,))
        return self.cursor.rowcount > 0

    def get_rules(self):
        """
        Все пользовательские правила
        """
        self.cursor.execute('SELECT id, rule_type, rule_value, weight FROM rules ORDER BY rule_type, id;')
        return self.cursor.fetchall()

    def close(self):
        """
        Закрытие соединения
        """
        self.cursor.close()
        self.conn.close()
