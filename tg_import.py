import json
import os

import config


class Telegram_Export_Parser:
    ADMIN_ACTIONS = {"topic_created", "topic_edit", "pin_message"}

    def __init__(self, my_user_id):
        """
        Разбор экспорта Telegram Desktop в формате JSON (result.json).
        Поддерживается экспорт одного чата и полный экспорт аккаунта
        """
        self.my_id = f"user{my_user_id}" if my_user_id else None

    def load(self, path: str) -> dict:
        """
        Чтение файла экспорта.
        """
        if os.path.isdir(path):
            path = os.path.join(path, "result.json")
        with open(path, encoding="utf-8") as file:
            return json.load(file)

    def get_chats(self, data: dict) -> list[dict]:
        """
        Список чатов для импорта.
        Экспорт одного чата — это сам чат. В полном экспорте берутся личные чаты (если WATCH_ALL_PERSONAL)
        и группы из WATCH_CHATS (пустой список = все группы)
        """
        if "messages" in data:
            return [data]

        chats = []
        for chat in data.get("chats", {}).get("list", []):
            name = self.chat_name(chat).lower()
            if chat.get("type") == "personal_chat":
                if config.WATCH_ALL_PERSONAL:
                    chats.append(chat)
            elif chat.get("type") != "saved_messages":
                if not config.WATCH_CHATS or any(watch.lower() in name for watch in config.WATCH_CHATS):
                    chats.append(chat)
        return chats

    def chat_name(self, chat: dict) -> str:
        """
        Название чата
        """
        return chat.get("name") or config.UNKNOWN_CHAT

    def flatten_text(self, text) -> str:
        """
        Текст в экспорте — строка или список кусков (строки и словари с форматированием).
        Склеиваем всё в одну строку
        """
        if isinstance(text, str):
            return text
        return "".join(part if isinstance(part, str) else part.get("text", "") for part in text)

    def find_topics(self, messages: list[dict]) -> dict:
        """
        Темы форума: id служебного сообщения "тема создана" -> название темы.
        Если тему переименовали (topic_edit с новым title), берётся новое название
        """
        topics = {}
        for msg in messages:
            if msg.get("action") == "topic_created":
                topics[msg["id"]] = msg.get("title")
            elif msg.get("action") == "topic_edit" and msg.get("title") and msg.get("reply_to_message_id") in topics:
                topics[msg["reply_to_message_id"]] = msg["title"]
        return topics

    def find_admins(self, messages: list[dict]) -> set:
        """
        Админы чата: те, кто создавал/менял темы или закреплял сообщения (обычным участникам это недоступно)
        """
        return {msg["actor_id"] for msg in messages if msg.get("action") in self.ADMIN_ACTIONS and msg.get("actor_id")}

    def find_pinned(self, messages: list[dict]) -> set:
        """
        id закреплённых сообщений (по служебным сообщениям "закрепил сообщение")
        """
        return {msg["message_id"] for msg in messages if msg.get("action") == "pin_message"}

    def get_topic(self, message_id: int, reply_to: dict, topics: dict, cache: dict):
        """
        Определение темы сообщения по цепочке ответов.
        В форуме каждое сообщение — ответ либо на "тема создана", либо на другое сообщение этой же темы.
        Идём вверх по цепочке, пока не дойдём до темы. Найденные темы запоминаются в cache,
        поэтому каждая цепочка проходится один раз
        """
        path = []
        current = message_id
        topic = config.GENERAL_TOPIC
        while current is not None:
            if current in topics:
                topic = topics[current]
                break
            if current in cache:
                topic = cache[current]
                break
            path.append(current)
            current = reply_to.get(current)
        for visited in path:
            cache[visited] = topic
        return topic

    def detect_media(self, msg: dict):
        """
        Вложение, по которому сообщение может оказаться шумом: стикер, фото, гифка, кружок
        """
        if msg.get("media_type") in ("sticker", "animation", "video_message"):
            return msg["media_type"]
        if "photo" in msg:
            return "photo"
        return None

    def parse_chat(self, chat: dict) -> tuple[list[dict], set]:
        """
        Разбор одного чата
        Возвращает:
            (список сообщений в формате базы данных, множество id админов)
        """
        messages = chat.get("messages", [])
        topics = self.find_topics(messages)
        admins = self.find_admins(messages)
        pinned = self.find_pinned(messages)
        reply_to = {msg["id"]: msg.get("reply_to_message_id") for msg in messages}
        cache = {}

        records = []
        for msg in messages:
            if msg.get("type") != "message":
                continue
            from_id = msg.get("from_id") or ""
            records.append({
                "chat_id": chat["id"],
                "chat_name": self.chat_name(chat),
                "chat_type": chat.get("type"),
                "topic": self.get_topic(msg["id"], reply_to, topics, cache) if topics else None,
                "message_id": msg["id"],
                "sender": msg.get("from") or config.UNKNOWN_SENDER,
                "sender_id": from_id,
                "date": msg["date"].replace("T", " "),
                "text": self.flatten_text(msg.get("text", "")),
                "reply_to": msg.get("reply_to_message_id"),
                "media": self.detect_media(msg),
                "is_mine": int(from_id == self.my_id),
                "is_admin": int(from_id in admins),
                "is_pinned": int(msg["id"] in pinned),
            })
        return records, admins

    def parse(self, path: str):
        """
        Главная функция: разбирает весь файл экспорта
        Возвращает:
            список (chat_id, название, сообщения, админы) по каждому чату
        """
        data = self.load(path)
        result = []
        for chat in self.get_chats(data):
            records, admins = self.parse_chat(chat)
            result.append((chat["id"], self.chat_name(chat), records, admins))
        return result
