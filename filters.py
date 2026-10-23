import re
from datetime import datetime, timedelta

import config


class Date_Parser:
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
        r"(?<![\d.,:/])(\d{1,2})\.(\d{2})(?:\.(\d{4}|\d{2}))?(?!\d|[./]\d)"
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
        r"присла\w*|успе\w*|крайн\w*|не позднее|включительно|дз|домашн\w*|домашк\w*|лаб\w*)\b",
        re.IGNORECASE
    )
    RE_WEAK_DEADLINE = re.compile(r"\bдо\b", re.IGNORECASE)
    RE_EVENT_WORDS = re.compile(
        r"\b(экзамен\w*|зач[её]т\w*|контрольн\w*|коллоквиум\w*|пар[аыу]|лекци\w*|семинар\w*|встреч\w*|"
        r"созвон\w*|собрани\w*|консультаци\w*|защит\w*|вебинар\w*|пересдач\w*)\b",
        re.IGNORECASE
    )

    WORD_NUMBERS = {"пару": 2, "одну": 1, "две": 2, "три": 3}
    DAYS_IN_WEEK = 7
    MONTHS_IN_YEAR = 12
    MAX_DAY_IN_MONTH = 31
    CENTURY = 2000

    def __init__(self, default_hour: int = config.DEFAULT_DEADLINE_HOUR,
                 default_minute: int = config.DEFAULT_DEADLINE_MINUTE):
        """
        Извлечение дат и дедлайнов из текста регулярками
        Если время не указано — ставится default_hour:default_minute (по умолчанию 23:59 из config.py)
        """
        self.default_hour = default_hour
        self.default_minute = default_minute

    def _day_start(self, moment: datetime) -> datetime:
        """
        Начало дня (00:00) для момента moment
        """
        return datetime(moment.year, moment.month, moment.day)

    def _build_date(self, day: int, month: int, year, base: datetime):
        """
        Сборка даты. Если год не указан: берётся год сообщения,
        а если дата получилась сильно в прошлом — следующий год (декабрь -> январь)
        """
        try:
            if year is None:
                result = datetime(base.year, month, day)
                if result < base - timedelta(days=config.YEAR_ROLLOVER_DAYS):
                    result = datetime(base.year + 1, month, day)
                return result
            year = int(year)
            if year < self.CENTURY:
                year += self.CENTURY
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
        Поиск времени (18:00, до 23.59) в тексте.
        Время через точку берётся, только если после точки не может стоять месяц (00 или больше 12):
        "в 18.30" — время, а "с 21.12" — дата
        Возвращает:
            (часы, минуты, текст_без_времени) или (None, None, текст)
        """
        match = self.RE_TIME.search(text)
        if match is None:
            for candidate in self.RE_TIME_DOT.finditer(text):
                minutes = int(candidate.group(2))
                if minutes == 0 or minutes > self.MONTHS_IN_YEAR:
                    match = candidate
                    break
        if match is None:
            return None, None, text
        cleaned = text[:match.start()] + " " + text[match.end():]
        return int(match.group(1)), int(match.group(2)), cleaned

    def find_absolute_dates(self, text: str, base: datetime) -> list[tuple]:
        """
        Абсолютные даты: 2026-10-25, 25.10, 25.10.2026, 25/10/2026, 25 октября
        Формат 25/10 без года не берётся — слишком часто это оценка (8/10), а не дата.
        Месяц в формате 25.10 — только две цифры, а дата без года — не дальше MAX_DAYS_AHEAD дней вперёд:
        иначе дробные числа вроде "2.2 балла" или "1.04" превращаются в даты
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
                if 1 <= day <= self.MAX_DAY_IN_MONTH and 1 <= month <= self.MONTHS_IN_YEAR:
                    date = self._build_date(day, month, match.group(3), base)
                    if date is None:
                        continue
                    if match.group(3) is None and date - base > timedelta(days=config.MAX_DAYS_AHEAD):
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
        start = self._day_start(base)

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
            days = amount * self.DAYS_IN_WEEK if unit.startswith("недел") else amount
            if 0 < days <= config.MAX_DAYS_AHEAD:
                found.append((start + timedelta(days=days), match.group(0)))

        for match in self.RE_WEEKDAY.finditer(text):
            word = match.group(1).lower()
            target = next((num for root, num in self.WEEKDAYS.items() if word.startswith(root)), None)
            if target is None:
                continue
            shift = (target - start.weekday()) % self.DAYS_IN_WEEK
            if shift == 0:
                shift = self.DAYS_IN_WEEK
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
        иначе "увидимся завтра" превращалось бы в дедлайн.
        Вид: 'deadline', если есть сильное слово (сдать, дедлайн, срок) или "до" без слов-событий; иначе 'event'.
        Даты раньше дня сообщения пропускаются: "сдавал 13.10" — это ссылка на прошлое, а не дедлайн
        """
        if not text or base is None:
            return []

        hour, minute, text_wo_time = self._find_time(text)
        is_event = self.is_event_text(text)
        is_deadline = self.is_deadline_text(text) or (bool(self.RE_WEAK_DEADLINE.search(text)) and not is_event)

        found = self.find_absolute_dates(text_wo_time, base)
        if is_deadline or is_event:
            found += self.find_relative_dates(text_wo_time, base)
            if not found and hour is not None:
                found.append((self._day_start(base), "сегодня"))

        if not found:
            return []

        kind = "deadline" if is_deadline else "event"
        message_day = self._day_start(base)
        result, seen = [], set()
        for date, raw in found:
            if date < message_day:
                continue
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
    FILTERS = [
        "mention_me", "name_in_text", "reply_to_me", "personal_chat", "vip_sender", "keywords",
        "deadline", "deadline_soon", "question_to_me", "announcement", "topic", "noise",
    ]

    RE_QUESTION_START = re.compile(
        r"^\s*(кто|что|когда|где|куда|как|почему|зачем|сколько|какой|какая|какие|"
        r"можешь|можно|сможешь|подскажи|скажи|знаешь|есть ли|будешь)\b",
        re.IGNORECASE
    )
    RE_ANNOUNCEMENT = re.compile(
        r"@all\b|@everyone\b|#важно|#объявлени\w*|\bвнимание\b|\bобъявлени\w*|"
        r"\bнапоминаю\b|\bнапоминание\b|\bуважаемые\b|\bколлеги\b",
        re.IGNORECASE
    )
    RE_HAS_WORD = re.compile(r"[a-zа-яё0-9]", re.IGNORECASE)
    RE_LAUGH = re.compile(r"^(а?х[аеи]+|ха+|ах+|ору|xd|[)(]+|\.+)$", re.IGNORECASE)

    def __init__(self, my_keys: set):
        """
        Фильтры важности. Каждый фильтр — метод, который получает сообщение (dict)
        и возвращает (баллы, причина) или None, если не сработал.
        Все настройки берутся из config.py в момент создания объекта.
        my_keys — множество (chat_id, message_id) моих сообщений, нужно фильтру reply_to_me
        """
        self.my_keys = my_keys
        self.parser = Date_Parser()
        self.weights = config.WEIGHTS
        self.my_username = config.MY_USERNAME.lower()

        names = "|".join(re.escape(name.lower()) for name in config.MY_NAMES)
        self.re_my_names = re.compile(rf"(?<![\w@])({names})\w*", re.IGNORECASE) if names else None

        self.keyword_patterns = [(re.compile(pattern, re.IGNORECASE), weight) for pattern, weight in config.KEYWORDS.items()]
        self.vip_senders = [str(vip).lower() for vip in config.VIP_SENDERS]
        self.noise_words = set(word.lower() for word in config.NOISE_WORDS)

    def is_question(self, msg: dict) -> bool:
        """
        Похоже ли сообщение на вопрос: есть "?" или начинается с вопросительного слова
        """
        return "?" in msg["text"] or bool(self.RE_QUESTION_START.search(msg["text"]))

    def is_addressed_to_me(self, msg: dict) -> bool:
        """
        Обращено ли сообщение лично ко мне: личка, упоминание, имя в тексте или ответ на моё сообщение
        """
        return any(check(msg) is not None for check in (
            self.personal_chat, self.mention_me, self.name_in_text, self.reply_to_me
        ))

    def mention_me(self, msg: dict):
        """
        1. Меня упомянули через @username
        """
        if self.my_username in msg["text"].lower():
            return self.weights["mention_me"], f"упоминание {config.MY_USERNAME}"
        return None

    def name_in_text(self, msg: dict):
        """
        2. Меня назвали по имени (корни имён из MY_NAMES)
        """
        if self.re_my_names is None:
            return None
        match = self.re_my_names.search(msg["text"])
        if match:
            return self.weights["name_in_text"], f"обращение по имени: {match.group(0)}"
        return None

    def reply_to_me(self, msg: dict):
        """
        3. Ответ на моё сообщение
        """
        if (msg["chat_id"], msg["reply_to"]) in self.my_keys:
            return self.weights["reply_to_me"], "ответ на моё сообщение"
        return None

    def personal_chat(self, msg: dict):
        """
        4. Сообщение в личке
        """
        if msg["chat_type"] == "personal_chat":
            return self.weights["personal_chat"], "личный чат"
        return None

    def vip_sender(self, msg: dict):
        """
        5. VIP-отправитель: админ чата (если AUTO_VIP_ADMINS) или есть в VIP_SENDERS (часть имени или id)
        """
        if config.AUTO_VIP_ADMINS and msg["is_admin"]:
            return self.weights["vip_sender"], f"админ чата: {msg['sender']}"
        sender = msg["sender"].lower()
        for vip in self.vip_senders:
            if vip in sender or vip == msg["sender_id"].lower():
                return self.weights["vip_sender"], f"VIP-отправитель: {msg['sender']}"
        return None

    def keywords(self, msg: dict):
        """
        6. Ключевые слова из KEYWORDS: сумма их весов (не больше KEYWORDS_MAX)
        """
        total, found = 0, []
        for pattern, weight in self.keyword_patterns:
            match = pattern.search(msg["text"])
            if match:
                total += weight
                found.append(match.group(0).lower())
        if total:
            points = min(total, config.KEYWORDS_MAX) * self.weights["keywords"]
            return points, f"ключевые слова: {', '.join(found)}"
        return None

    def deadline(self, msg: dict):
        """
        7. В тексте есть дедлайн или дата события (их находит Date_Parser). Событие дешевле на EVENT_DISCOUNT
        """
        if not msg["deadlines"]:
            return None
        first = msg["deadlines"][0]
        when = first["date"].strftime(config.SHORT_DATE_FORMAT)
        if first["kind"] == "deadline":
            return self.weights["deadline"], f"дедлайн: {when}"
        return self.weights["deadline"] - config.EVENT_DISCOUNT, f"дата события: {when}"

    def deadline_soon(self, msg: dict):
        """
        8. Дедлайн ещё не прошёл и наступит в ближайшие SOON_DAYS дней (считается от текущего момента)
        """
        now = datetime.now()
        for item in msg["deadlines"]:
            if now <= item["date"] <= now + timedelta(days=config.SOON_DAYS):
                return self.weights["deadline_soon"], f"дедлайн скоро: {item['date'].strftime(config.SHORT_DATE_FORMAT)}"
        return None

    def question_to_me(self, msg: dict):
        """
        9. Вопрос, обращённый ко мне. Такие сообщения попадают в список "без ответа"
        """
        if self.is_question(msg) and self.is_addressed_to_me(msg):
            return self.weights["question_to_me"], "вопрос ко мне"
        return None

    def announcement(self, msg: dict):
        """
        10. Объявление: закреплённое сообщение, @all, "внимание", "напоминаю", "уважаемые коллеги"
        """
        if msg["is_pinned"]:
            return self.weights["announcement"], "закреплённое сообщение"
        match = self.RE_ANNOUNCEMENT.search(msg["text"])
        if match:
            return self.weights["announcement"], f"объявление: {match.group(0)}"
        return None

    def topic(self, msg: dict):
        """
        11. Важная тема форума (TOPIC_WEIGHTS), например "Дедлайны" или "Новости и анонсы"
        """
        topic_weight = config.TOPIC_WEIGHTS.get(msg["topic"], 0)
        if topic_weight:
            return topic_weight * self.weights["topic"], f"тема: {msg['topic']}"
        return None

    def noise(self, msg: dict):
        """
        Штраф за шум: "ок", "+", "ахах", стикер или фото без подписи, одни эмодзи
        """
        text = msg["text"].strip().lower().rstrip("!.,?)( ")
        if not text and msg["media"]:
            return self.weights["noise"], f"шум: {msg['media']} без текста"
        if text in self.noise_words or self.RE_LAUGH.match(text):
            return self.weights["noise"], "шум: короткий ответ"
        if text and not self.RE_HAS_WORD.search(text):
            return self.weights["noise"], "шум: только эмодзи"
        return None

    def score(self, msg: dict) -> dict:
        """
        Итоговая оценка: прогоняет сообщение через все фильтры и складывает баллы
        Возвращает:
            dict: score — сумма баллов, reasons — сработавшие фильтры,
                  deadlines — найденные даты, is_question — вопрос ко мне
        Мои собственные сообщения не оцениваются, но даты из них тоже достаются
        """
        msg["deadlines"] = self.parser.extract(msg["text"], msg["date"])
        result = {"score": 0, "reasons": [], "deadlines": msg["deadlines"], "is_question": False}
        if msg["is_mine"]:
            return result

        for name in self.FILTERS:
            if self.weights.get(name, 0) == 0:
                continue
            verdict = getattr(self, name)(msg)
            if verdict is None:
                continue
            points, reason = verdict
            result["score"] += points
            result["reasons"].append(f"{reason} ({points:+d})")
            if name == "question_to_me":
                result["is_question"] = True
        return result
