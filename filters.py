import re
from datetime import datetime, timedelta

import config


class Deadline_Extractor:
    MONTHS = {
        "янв": 1, "фев": 2, "мар": 3, "апр": 4, "мая": 5, "май": 5, "июн": 6,
        "июл": 7, "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12,
    }
    WEEKDAYS = {
        "понедельник": 0, "вторник": 1, "сред": 2, "четверг": 3,
        "пятниц": 4, "суббот": 5, "воскресень": 6,
    }

    RE_ISO = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
    RE_NUMERIC = re.compile(
        r"(?<![\d.,:/])(\d{1,2})\.(\d{1,2})(?:\.(\d{4}|\d{2}))?(?!\d|[./]\d)"
        r"(?!\s*(?:%|₽|\$|руб|р\.|кг|км|млн|тыс|балл|час|мин|раз|k\b|к\b))"
    )
    RE_SLASH = re.compile(r"(?<![\d.,:/])(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})(?![\d/])")
    RE_MONTH_WORD = re.compile(
        r"\b(\d{1,2})\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря|"
        r"янв|фев|мар|апр|июн|июл|авг|сент|сен|окт|нояб|ноя|дек)\.?(?:\s+(\d{4}))?",
        re.IGNORECASE
    )
    RE_RELATIVE_DAY = re.compile(r"\b(сегодня|послезавтра|завтра)\b", re.IGNORECASE)
    RE_IN_DAYS = re.compile(
        r"\bчерез\s+(\d+|пару|одну|две|три)?\s*(дн[еяй]+|день|недел[июья]+)\b",
        re.IGNORECASE
    )
    RE_WEEKDAY = re.compile(
        r"\b(?:в|во|до|к|ко|на)\s+(понедельник|вторник|сред[уыае]|четверг|пятниц[уыае]|суббот[уыае]|воскресень[еяю])",
        re.IGNORECASE
    )
    RE_TIME = re.compile(r"(?<![\d.:])([01]?\d|2[0-3]):([0-5]\d)(?![\d])")
    RE_TIME_DOT = re.compile(r"\b(?:до|в|к|с)\s+([01]?\d|2[0-3])\.([0-5]\d)\b", re.IGNORECASE)

    RE_DEADLINE_WORDS = re.compile(
        r"\b(дедлайн\w*|deadline|due|срок\w*|сдать|сдач\w*|сда[её]м|сда[её]т|загруз\w*|отправ\w*|"
        r"присла\w*|успе\w*|крайн\w*|до|не позднее|включительно|дз|домашк\w*|лаб\w*)\b",
        re.IGNORECASE
    )
    RE_EVENT_WORDS = re.compile(
        r"\b(экзамен\w*|зач[её]т\w*|контрольн\w*|коллоквиум\w*|пар[аыу]|лекци\w*|семинар\w*|встреч\w*|"
        r"созвон\w*|собрани\w*|консультаци\w*|защит\w*|вебинар\w*|пересдач\w*|дз|домашк\w*|лаб\w*)\b",
        re.IGNORECASE
    )

    WORD_NUMBERS = {"пару": 2, "одну": 1, "две": 2, "три": 3}

    def __init__(self, default_hour: int = 23, default_minute: int = 59):
        """
        Извлечение дат и дедлайнов из текста регулярками
        Если время не указано — ставится default_hour:default_minute (по умолчанию 23:59)
        """
        self.default_hour = default_hour
        self.default_minute = default_minute

    def _build_date(self, day: int, month: int, year, base: datetime):
        """
        Сборка даты. Если год не указан: берётся год сообщения,
        а если дата получилась сильно в прошлом — следующий год (декабрь -> январь)
        """
        try:
            if year is None:
                result = datetime(base.year, month, day)
                if result < base - timedelta(days=60):
                    result = datetime(base.year + 1, month, day)
                return result
            year = int(year)
            if year < 100:
                year += 2000
            return datetime(year, month, day)
        except ValueError:
            return None

    def _month_from_word(self, word: str):
        """
        Номер месяца по слову ("октября", "окт" -> 10)
        """
        word = word.lower()
        if word.startswith("ма"):
            return 5
        return self.MONTHS.get(word[:3])

    def _find_time(self, text: str):
        """
        Поиск времени (18:00, до 23.59) в тексте
        Возвращает:
            (часы, минуты, текст_без_времени) или (None, None, текст)
        """
        match = self.RE_TIME.search(text) or self.RE_TIME_DOT.search(text)
        if not match:
            return None, None, text
        cleaned = text[:match.start()] + " " + text[match.end():]
        return int(match.group(1)), int(match.group(2)), cleaned

    def find_absolute_dates(self, text: str, base: datetime) -> list[tuple]:
        """
        Абсолютные даты: 2026-10-25, 25.10, 25.10.2026, 25/10/2026, 25 октября
        Формат 25/10 без года не берётся — слишком часто это оценка (8/10), а не дата
        Возвращает:
            список (datetime, исходный_фрагмент)
        """
        found = []
        for match in self.RE_ISO.finditer(text):
            date = self._build_date(int(match.group(3)), int(match.group(2)), match.group(1), base)
            if date:
                found.append((date, match.group(0)))
        text_wo_iso = self.RE_ISO.sub(" ", text)

        for pattern in (self.RE_NUMERIC, self.RE_SLASH):
            for match in pattern.finditer(text_wo_iso):
                day, month = int(match.group(1)), int(match.group(2))
                if 1 <= day <= 31 and 1 <= month <= 12:
                    date = self._build_date(day, month, match.group(3), base)
                    if date is None:
                        continue
                    if match.group(3) is None and date - base > timedelta(days=180):
                        continue
                    found.append((date, match.group(0)))

        for match in self.RE_MONTH_WORD.finditer(text):
            month = self._month_from_word(match.group(2))
            if month:
                date = self._build_date(int(match.group(1)), month, match.group(3), base)
                if date:
                    found.append((date, match.group(0)))
        return found

    def find_relative_dates(self, text: str, base: datetime) -> list[tuple]:
        """
        Относительные даты: сегодня, завтра, послезавтра, через 3 дня, через неделю, до пятницы
        Отсчёт идёт от даты сообщения, а не от сегодняшнего дня
        """
        found = []
        start = datetime(base.year, base.month, base.day)

        for match in self.RE_RELATIVE_DAY.finditer(text):
            word = match.group(1).lower()
            shift = {"сегодня": 0, "завтра": 1, "послезавтра": 2}[word]
            found.append((start + timedelta(days=shift), match.group(0)))

        for match in self.RE_IN_DAYS.finditer(text):
            amount_raw = (match.group(1) or "1").lower()
            if amount_raw.isdigit():
                amount = int(amount_raw)
            else:
                amount = self.WORD_NUMBERS.get(amount_raw, 1)
            unit = match.group(2).lower()
            days = amount * 7 if unit.startswith("недел") else amount
            if 0 < days <= 120:
                found.append((start + timedelta(days=days), match.group(0)))

        for match in self.RE_WEEKDAY.finditer(text):
            word = match.group(1).lower()
            target = next((num for root, num in self.WEEKDAYS.items() if word.startswith(root)), None)
            if target is None:
                continue
            shift = (target - start.weekday()) % 7
            if shift == 0:
                shift = 7
            found.append((start + timedelta(days=shift), match.group(0)))
        return found

    def is_deadline_text(self, text: str) -> bool:
        """
        Есть ли в тексте слова, указывающие на дедлайн (сдать, до, срок, дедлайн...)
        """
        return bool(self.RE_DEADLINE_WORDS.search(text))

    def is_event_text(self, text: str) -> bool:
        """
        Есть ли в тексте слова-события (экзамен, пара, встреча, созвон...)
        """
        return bool(self.RE_EVENT_WORDS.search(text))

    def extract(self, text: str, base: datetime) -> list[dict]:
        """
        Главная функция: все дедлайны/события из текста
        Параметры:
            text: текст сообщения
            base: дата сообщения (от неё считаются "завтра", "через 3 дня")
        Возвращает:
            список словарей {date, kind, raw}, kind = 'deadline' или 'event'
        Относительные даты ("завтра") берутся только если рядом есть слова дедлайна/события,
        иначе "увидимся завтра" превращалось бы в дедлайн
        """
        if not text or base is None:
            return []

        hour, minute, text_wo_time = self._find_time(text)
        is_deadline = self.is_deadline_text(text)
        is_event = self.is_event_text(text)

        found = self.find_absolute_dates(text_wo_time, base)
        if is_deadline or is_event:
            found += self.find_relative_dates(text_wo_time, base)
            if not found and hour is not None:
                found.append((datetime(base.year, base.month, base.day), "сегодня"))

        if not found:
            return []

        kind = "deadline" if is_deadline else "event"
        result, seen = [], set()
        for date, raw in found:
            if hour is not None:
                date = date.replace(hour=hour, minute=minute)
            else:
                date = date.replace(hour=self.default_hour, minute=self.default_minute)
            if date in seen:
                continue
            seen.add(date)
            raw_text = raw if hour is None else f"{raw} {hour:02d}:{minute:02d}"
            result.append({"date": date, "kind": kind, "raw": raw_text})
        return sorted(result, key=lambda item: item["date"])


class Message_Filters:
    RE_QUESTION_START = re.compile(
        r"^\s*(кто|что|когда|где|куда|как|почему|зачем|сколько|какой|какая|какие|можешь|можно|сможешь|"
        r"подскажи|скажи|знаешь|есть ли|будешь|ты\s+\w+\s+\?)",
        re.IGNORECASE
    )
    RE_URGENCY = re.compile(r"!{2,}|‼|❗|🔥|🚨|⚠|⛔|\bsos\b", re.IGNORECASE)
    RE_MASS_MENTION = re.compile(
        r"@all\b|@everyone\b|@here\b|@channel\b|\bвсем внимание|\bвнимание,? всем|\bвсем срочно|\bвсе сюда|\bвнимание,? группа",
        re.IGNORECASE
    )
    RE_ANNOUNCEMENT = re.compile(
        r"#важно|#объявлени|#announcement|#дедлайн|\bвнимание\b|\bобъявлени\w*|\bнапоминаю\b|\bнапоминание\b|"
        r"\bинформаци[яю] для\b|\bуважаемые\b|\bколлеги\b",
        re.IGNORECASE
    )
    RE_SCHEDULE_CHANGE = re.compile(
        r"\bперенос\w*|\bперенес\w*|\bотмен\w*|\bвместо\b|\bаудитори\w*|\bауд\.|\bне будет\b|\bзамен[аеуы]\b|\bпереезжа\w*",
        re.IGNORECASE
    )
    RE_MONEY = re.compile(
        r"\bоплат\w*|\bзаплат\w*|\bперевед\w*|\bперевест\w*|\bдолг\w*|\bскинуться|\bскидываемся|"
        r"\d+\s?(?:₽|руб\w*|р\.)|\bвзнос\w*",
        re.IGNORECASE
    )
    RE_TIME = re.compile(r"(?<![\d.:])([01]?\d|2[0-3]):([0-5]\d)(?!\d)")
    RE_LAUGH = re.compile(r"^(?:а?х[аеи]+|ха+|ах+|лол|кек|ору|xd|ахах\w*|хах\w*|[)(]+|\.+)$", re.IGNORECASE)
    RE_HAS_WORD = re.compile(r"[a-zа-яё0-9]", re.IGNORECASE)

    IMPORTANT_DOMAINS = (
        "docs.google", "forms.gle", "forms.yandex", "disk.yandex", "drive.google", "lms.", "anytask",
        "contest.", "wiki.", "zoom.us", "meet.google", "telemost", "teams.microsoft", "notion.", "github.com",
        "classroom.google", "edu.", "smartlms",
    )
    DOCUMENT_EXTENSIONS = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip", ".rar", ".ipynb", ".py", ".txt", ".csv")
    MEDIA_NOISE = ("sticker", "animation", "video_message")

    def __init__(self, weights: dict = None, context: dict = None):
        """
        Набор фильтров важности. Каждый фильтр получает сообщение (dict)
        и возвращает (баллы, причина) или None, если не сработал.
        Параметры:
            weights: веса фильтров (по умолчанию config.FILTER_WEIGHTS)
            context: данные из БД, нужные некоторым фильтрам:
                chat_info — chat_id -> {name, type, priority, is_muted}
                my_keys — множество (chat_id, message_id) моих сообщений
                reply_counts — (chat_id, message_id) -> сколько ответов
                last_my_dates — chat_id -> дата моего последнего сообщения
                rules — пользовательские правила из таблицы rules
                now — "текущий момент" (для дедлайнов и старых сообщений)
        """
        self.weights = dict(config.FILTER_WEIGHTS if weights is None else weights)
        context = context or {}
        self.chat_info = context.get("chat_info", {})
        self.my_keys = context.get("my_keys", set())
        self.reply_counts = context.get("reply_counts", {})
        self.last_my_dates = context.get("last_my_dates", {})
        self.now = context.get("now") or datetime.now()

        self.my_user_id = context.get("my_user_id", config.MY_USER_ID)
        self.my_username = (config.MY_USERNAME or "").lower()
        if self.my_username and not self.my_username.startswith("@"):
            self.my_username = "@" + self.my_username
        self.re_my_names = None
        if config.MY_NAMES:
            names = "|".join(re.escape(name.lower()) for name in config.MY_NAMES)
            self.re_my_names = re.compile(rf"(?<![\w@])({names})\w*", re.IGNORECASE)

        self.keyword_patterns = [(re.compile(pattern, re.IGNORECASE), weight) for pattern, weight in config.KEYWORDS.items()]
        self.vip_senders = [(name.lower(), None) for name in config.VIP_SENDERS]
        self.vip_chats = [(name.lower(), None) for name in config.VIP_CHATS]
        self.noise_words = set(word.lower() for word in config.NOISE_WORDS)

        for rule_id, rule_type, value, weight in context.get("rules", []):
            if rule_type == "keyword":
                self.keyword_patterns.append((re.compile(value, re.IGNORECASE), weight))
            elif rule_type == "sender":
                self.vip_senders.append((value.lower(), weight))
            elif rule_type == "chat":
                self.vip_chats.append((value.lower(), weight))

    def w(self, name: str) -> int:
        """
        Вес фильтра (0, если фильтра нет в конфиге)
        """
        return self.weights.get(name, 0)

    def _chat(self, msg: dict) -> dict:
        """
        Информация о чате сообщения
        """
        return self.chat_info.get(msg["chat_id"], {})

    def _text(self, msg: dict) -> str:
        """
        Текст сообщения (пустая строка, если текста нет)
        """
        return msg.get("msg_text") or ""

    def _short(self, value: str, length: int = 30) -> str:
        """
        Обрезка строки для вывода в причинах
        """
        value = value.replace("\n", " ").strip()
        return value if len(value) <= length else value[:length - 1] + "…"

    def is_personal(self, msg: dict) -> bool:
        """
        Сообщение из личного чата (не с ботом)
        """
        return self._chat(msg).get("type") == "personal_chat"

    def is_question(self, msg: dict) -> bool:
        """
        Похоже ли сообщение на вопрос
        """
        text = self._text(msg)
        return "?" in text or bool(self.RE_QUESTION_START.search(text))

    def is_mentioning_me(self, msg: dict) -> bool:
        """
        Упомянут ли я через @username или mention_name
        """
        mentions = msg.get("mentions") or []
        if self.my_username and self.my_username != "@your_username":
            if self.my_username in mentions or self.my_username in self._text(msg).lower():
                return True
        if self.my_user_id and f"user:{self.my_user_id}" in mentions:
            return True
        return False

    def is_reply_to_me(self, msg: dict) -> bool:
        """
        Является ли сообщение ответом на моё сообщение
        """
        return msg.get("reply_to") is not None and (msg["chat_id"], msg["reply_to"]) in self.my_keys

    def is_addressed_to_me(self, msg: dict) -> bool:
        """
        Обращено ли сообщение лично ко мне: личка, упоминание, ответ мне или имя в тексте
        """
        return (
            self.is_personal(msg)
            or self.is_mentioning_me(msg)
            or self.is_reply_to_me(msg)
            or (self.re_my_names is not None and bool(self.re_my_names.search(self._text(msg))))
        )

    def mention_me(self, msg: dict):
        """
        Меня упомянули через @username
        """
        if self.is_mentioning_me(msg):
            return self.w("mention_me"), f"упоминание: {self.my_username}"
        return None

    def name_in_text(self, msg: dict):
        """
        Меня назвали по имени (Булат, Булату, Bulat...)
        """
        if self.re_my_names is None:
            return None
        match = self.re_my_names.search(self._text(msg))
        if match:
            return self.w("name_in_text"), f"обращение по имени: {match.group(0)}"
        return None

    def reply_to_me(self, msg: dict):
        """
        Ответ на моё сообщение
        """
        if self.is_reply_to_me(msg):
            return self.w("reply_to_me"), "ответ на моё сообщение"
        return None

    def personal_chat(self, msg: dict):
        """
        Сообщение в личке
        """
        if self.is_personal(msg):
            return self.w("personal_chat"), "личный чат"
        return None

    def question_to_me(self, msg: dict):
        """
        Вопрос, обращённый ко мне (в личке, с упоминанием или ответом мне)
        """
        if self.is_question(msg) and self.is_addressed_to_me(msg):
            return self.w("question_to_me"), "вопрос ко мне"
        return None

    def unanswered_question(self, msg: dict):
        """
        Вопрос ко мне, после которого я ничего не писал в этот чат
        """
        if not (self.is_question(msg) and self.is_addressed_to_me(msg)):
            return None
        last_mine = self.last_my_dates.get(msg["chat_id"])
        if last_mine is None or last_mine < msg["msg_date"]:
            return self.w("unanswered_question"), "вопрос без ответа: я ещё не отвечал"
        return None

    def vip_sender(self, msg: dict):
        """
        Отправитель из списка VIP (преподаватели, руководитель...) — config.VIP_SENDERS и правила 'sender'
        """
        sender = (msg.get("sender_name") or "").lower()
        sender_id = (msg.get("sender_id") or "").lower()
        for value, weight in self.vip_senders:
            if value and (value in sender or value == sender_id):
                return (weight if weight is not None else self.w("vip_sender")), f"VIP-отправитель: {msg.get('sender_name')}"
        return None

    def priority_chat(self, msg: dict):
        """
        Приоритетный чат: ручной приоритет из таблицы chats или config.VIP_CHATS / правила 'chat'
        """
        chat = self._chat(msg)
        points = chat.get("priority", 0) * self.w("priority_chat")
        name = (chat.get("name") or "").lower()
        for value, weight in self.vip_chats:
            if value and value in name:
                points += weight if weight is not None else 3 * self.w("priority_chat")
                break
        if points:
            return points, f"приоритетный чат: {chat.get('name')}"
        return None

    def keywords(self, msg: dict):
        """
        Ключевые слова (срочно, экзамен, дедлайн...) — сумма весов найденных слов, максимум 6
        """
        text = self._text(msg)
        if not text:
            return None
        total, found = 0, []
        for pattern, weight in self.keyword_patterns:
            match = pattern.search(text)
            if match:
                total += weight
                found.append(match.group(0).lower())
        if total:
            return min(total, 6) * self.w("keywords"), f"ключевые слова: {', '.join(found[:4])}"
        return None

    def deadline(self, msg: dict):
        """
        В сообщении есть дедлайн или дата события (их находит Deadline_Extractor)
        """
        deadlines = msg.get("_deadlines") or []
        if not deadlines:
            return None
        first = deadlines[0]
        if any(item["kind"] == "deadline" for item in deadlines):
            return self.w("deadline"), f"дедлайн: {first['date']:%d.%m %H:%M}"
        return max(self.w("deadline") - 1, 0), f"дата события: {first['date']:%d.%m %H:%M}"

    def deadline_soon(self, msg: dict):
        """
        Дедлайн ещё не прошёл и наступит в ближайшие DEADLINE_SOON_DAYS дней
        """
        horizon = self.now + timedelta(days=config.DEADLINE_SOON_DAYS)
        for item in msg.get("_deadlines") or []:
            if self.now <= item["date"] <= horizon:
                return self.w("deadline_soon"), f"дедлайн скоро: {item['date']:%d.%m %H:%M}"
        return None

    def urgency_marks(self, msg: dict):
        """
        Восклицательные знаки, 🔥, ❗, 🚨 и т.п.
        """
        if self.RE_URGENCY.search(self._text(msg)):
            return self.w("urgency_marks"), "знаки срочности"
        return None

    def caps_lock(self, msg: dict):
        """
        Сообщение написано КАПСОМ (больше 70% заглавных, минимум 10 букв)
        """
        letters = [ch for ch in self._text(msg) if ch.isalpha()]
        if len(letters) >= 10 and sum(ch.isupper() for ch in letters) / len(letters) > 0.7:
            return self.w("caps_lock"), "капслок"
        return None

    def mass_mention(self, msg: dict):
        """
        Обращение ко всем: @all, "всем внимание", "внимание, группа"
        """
        match = self.RE_MASS_MENTION.search(self._text(msg))
        if match:
            return self.w("mass_mention"), f"обращение ко всем: {match.group(0)}"
        return None

    def pinned(self, msg: dict):
        """
        Сообщение закрепили в чате
        """
        if msg.get("is_pinned"):
            return self.w("pinned"), "закреплённое сообщение"
        return None

    def announcement(self, msg: dict):
        """
        Похоже на объявление: #важно, "внимание", "напоминаю", "уважаемые коллеги"
        """
        match = self.RE_ANNOUNCEMENT.search(self._text(msg))
        if match:
            return self.w("announcement"), f"объявление: {match.group(0)}"
        return None

    def important_links(self, msg: dict):
        """
        Ссылки на документы, формы, LMS, созвоны
        """
        haystack = " ".join(msg.get("links") or []) + " " + self._text(msg)
        haystack = haystack.lower()
        for domain in self.IMPORTANT_DOMAINS:
            if domain in haystack:
                return self.w("important_links"), f"важная ссылка: {domain.strip('.')}"
        return None

    def document_file(self, msg: dict):
        """
        Прикреплён документ (pdf, docx, xlsx, zip...)
        """
        file_name = (msg.get("file_name") or "").lower()
        if file_name.endswith(self.DOCUMENT_EXTENSIONS):
            return self.w("document_file"), f"документ: {self._short(msg['file_name'], 25)}"
        return None

    def schedule_change(self, msg: dict):
        """
        Изменения расписания: перенос, отмена, другая аудитория, "пары не будет"
        """
        match = self.RE_SCHEDULE_CHANGE.search(self._text(msg))
        if match:
            return self.w("schedule_change"), f"изменение планов: {match.group(0)}"
        return None

    def meeting_time(self, msg: dict):
        """
        Конкретное время + слово-событие (созвон в 18:00, пара в 9:30)
        """
        text = self._text(msg)
        if self.RE_TIME.search(text) and Deadline_Extractor.RE_EVENT_WORDS.search(text):
            return self.w("meeting_time"), "встреча/событие со временем"
        return None

    def money(self, msg: dict):
        """
        Деньги: оплатить, перевести, скинуться, суммы в рублях
        """
        match = self.RE_MONEY.search(self._text(msg))
        if match:
            return self.w("money"), f"деньги: {match.group(0)}"
        return None

    def popular_reactions(self, msg: dict):
        """
        Много реакций — сообщение заметное
        """
        count = msg.get("reactions_count") or 0
        if count >= config.POPULAR_REACTIONS_MIN:
            return self.w("popular_reactions"), f"много реакций: {count}"
        return None

    def hot_discussion(self, msg: dict):
        """
        На сообщение много ответили — обсуждение
        """
        count = self.reply_counts.get((msg["chat_id"], msg["message_id"]), 0)
        if count >= config.HOT_DISCUSSION_MIN_REPLIES:
            return self.w("hot_discussion"), f"обсуждение: {count} ответов"
        return None

    def long_message(self, msg: dict):
        """
        Длинное сообщение в группе — часто это объявление или инструкция
        """
        if len(self._text(msg)) >= config.LONG_MESSAGE_MIN_CHARS and not self.is_personal(msg):
            return self.w("long_message"), "длинное сообщение"
        return None

    def noise_short(self, msg: dict):
        """
        Шум: "ок", "+", "спасибо", "ахах" и т.п.
        """
        text = self._text(msg).strip().lower()
        normalized = re.sub(r"[!.,?\s]+$", "", text)
        if normalized and (normalized in self.noise_words or self.RE_LAUGH.match(normalized)):
            return self.w("noise_short"), "шум: короткий ответ"
        return None

    def media_without_text(self, msg: dict):
        """
        Стикер, гифка, кружок или фото без подписи
        """
        media = msg.get("media_type")
        if media in self.MEDIA_NOISE:
            return self.w("media_without_text"), f"шум: {media}"
        if media == "photo" and not self._text(msg).strip():
            return self.w("media_without_text"), "шум: фото без подписи"
        return None

    def emoji_only(self, msg: dict):
        """
        Текст состоит только из эмодзи/символов
        """
        text = self._text(msg).strip()
        if text and msg.get("media_type") not in self.MEDIA_NOISE and not self.RE_HAS_WORD.search(text):
            return self.w("emoji_only"), "шум: только эмодзи"
        return None

    def forwarded(self, msg: dict):
        """
        Пересланное сообщение (часто мемы и репосты)
        """
        if msg.get("forwarded_from"):
            return self.w("forwarded"), f"переслано из: {self._short(str(msg['forwarded_from']), 25)}"
        return None

    def bot_message(self, msg: dict):
        """
        Сообщение от бота или через бота
        """
        sender = (msg.get("sender_name") or "").lower()
        if msg.get("via_bot") or self._chat(msg).get("type") == "bot_chat" or sender.endswith("bot"):
            return self.w("bot_message"), "бот"
        return None

    def muted_chat(self, msg: dict):
        """
        Чат заглушён командой mute
        """
        if self._chat(msg).get("is_muted"):
            return self.w("muted_chat"), "заглушённый чат"
        return None

    def old_message(self, msg: dict):
        """
        Сообщение старше OLD_MESSAGE_DAYS дней (по умолчанию вес 0 — выключен)
        """
        if msg.get("msg_date") and self.now - msg["msg_date"] > timedelta(days=config.OLD_MESSAGE_DAYS):
            return self.w("old_message"), "старое сообщение"
        return None


class Importance_Scorer:
    FILTER_NAMES = [
        "mention_me", "name_in_text", "reply_to_me", "personal_chat", "question_to_me",
        "unanswered_question", "vip_sender", "priority_chat", "keywords", "deadline",
        "deadline_soon", "urgency_marks", "caps_lock", "mass_mention", "pinned",
        "announcement", "important_links", "document_file", "schedule_change", "meeting_time",
        "money", "popular_reactions", "hot_discussion", "long_message", "noise_short",
        "media_without_text", "emoji_only", "forwarded", "bot_message", "muted_chat", "old_message",
    ]

    def __init__(self, filters: Message_Filters, levels: dict = None, extractor: Deadline_Extractor = None):
        """
        Итоговая оценка: прогоняет сообщение через все включённые фильтры,
        складывает баллы и определяет уровень (critical / high / medium / low)
        """
        self.filters = filters
        self.levels = levels or config.LEVELS
        self.extractor = extractor or Deadline_Extractor()
        self.active = [
            (name, getattr(filters, name))
            for name in self.FILTER_NAMES
            if filters.w(name) != 0 and hasattr(filters, name)
        ]

    def get_level(self, score: int) -> str:
        """
        Уровень важности по сумме баллов
        """
        if score >= self.levels["critical"]:
            return "critical"
        if score >= self.levels["high"]:
            return "high"
        if score >= self.levels["medium"]:
            return "medium"
        return "low"

    def score_message(self, msg: dict) -> dict:
        """
        Оценка одного сообщения
        Возвращает:
            dict: {score, level, reasons, deadlines}
            Мои собственные сообщения не оцениваются (level = 'mine'), но дедлайны из них достаются
        """
        msg["_deadlines"] = self.extractor.extract(msg.get("msg_text") or "", msg.get("msg_date"))

        if msg.get("is_mine"):
            return {"score": 0, "level": "mine", "reasons": [], "deadlines": msg["_deadlines"]}

        score, reasons = 0, []
        for name, check in self.active:
            result = check(msg)
            if result is None:
                continue
            points, reason = result
            if points == 0:
                continue
            score += points
            reasons.append(f"{reason} ({points:+d})")

        return {
            "score": score,
            "level": self.get_level(score),
            "reasons": reasons,
            "deadlines": msg["_deadlines"],
        }


def build_scorer(db, now: datetime = None, my_user_id=None) -> Importance_Scorer:
    """
    Сборка оценщика с контекстом из базы данных
    """
    context = {
        "chat_info": db.get_chat_info_map(),
        "my_keys": db.get_my_message_keys(),
        "reply_counts": db.get_reply_counts(),
        "last_my_dates": db.get_last_my_message_dates(),
        "rules": db.get_rules(),
        "now": now or datetime.now(),
        "my_user_id": my_user_id or config.MY_USER_ID,
    }
    return Importance_Scorer(Message_Filters(config.FILTER_WEIGHTS, context))


def run_analysis(db, reanalyze: bool = False, my_user_id=None, verbose: bool = True) -> dict:
    """
    Оценка важности всех неоценённых сообщений (или всех заново при reanalyze=True)
    Возвращает:
        dict: количество по уровням + найдено дедлайнов
    """
    if reanalyze:
        db.clear_analysis()

    scorer = build_scorer(db, my_user_id=my_user_id)
    totals = {"critical": 0, "high": 0, "medium": 0, "low": 0, "mine": 0, "deadlines": 0}

    while True:
        batch = db.get_messages_for_analysis(only_new=True, limit=config.ANALYZE_BATCH_SIZE)
        if not batch:
            break

        results, deadlines = [], []
        for msg in batch:
            verdict = scorer.score_message(msg)
            totals[verdict["level"]] += 1
            results.append((
                msg["id"], verdict["score"], verdict["level"], verdict["reasons"],
                verdict["level"] in ("mine", "low")
            ))
            for item in verdict["deadlines"]:
                deadlines.append((msg["id"], item["date"], item["kind"], item["raw"]))

        db.save_analysis(results)
        db.save_deadlines(deadlines)
        totals["deadlines"] += len(deadlines)
        if verbose:
            print(f"  оценено: {sum(totals[k] for k in ('critical', 'high', 'medium', 'low', 'mine'))}")
    return totals
