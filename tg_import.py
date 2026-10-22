import json
import os
from datetime import datetime

import config


class Telegram_Export_Parser:
    def __init__(self, my_user_id=None, skip_chat_types=None):
        """
        Парсер экспорта Telegram Desktop (формат JSON: result.json)
        Поддерживает и полный экспорт (Settings -> Advanced -> Export Telegram data),
        и экспорт одного чата (меню чата -> Export chat history)
        """
        self.my_user_id = my_user_id
        self.skip_chat_types = set(skip_chat_types or [])

    def load(self, path: str) -> dict:
        """
        Чтение JSON-файла экспорта
        Можно передать путь к папке экспорта — внутри будет найден result.json
        """
        if os.path.isdir(path):
            path = os.path.join(path, "result.json")
        if not os.path.exists(path):
            raise FileNotFoundError(f"Файл экспорта не найден: {path}")
        with open(path, encoding="utf-8") as file:
            return json.load(file)

    def detect_my_id(self, data: dict):
        """
        Определение моего user_id: из config, из personal_information или из Избранного
        """
        if self.my_user_id:
            return self.my_user_id
        personal = data.get("personal_information") or {}
        if personal.get("user_id"):
            self.my_user_id = personal["user_id"]
            return self.my_user_id
        for chat in self.get_chat_list(data):
            if chat.get("type") == "saved_messages":
                for msg in chat.get("messages", []):
                    from_id = msg.get("from_id", "")
                    if from_id.startswith("user"):
                        self.my_user_id = int(from_id[4:])
                        return self.my_user_id
        return None

    def get_chat_list(self, data: dict) -> list[dict]:
        """
        Список чатов из экспорта (полный экспорт или один чат)
        """
        if "chats" in data:
            chats = data["chats"].get("list", [])
            left = data.get("left_chats", {}).get("list", [])
            return chats + left
        if "messages" in data:
            return [data]
        return []

    def flatten_text(self, text) -> str:
        """
        Текст в экспорте бывает строкой или списком кусков (строки + словари с форматированием)
        Функция склеивает всё в одну строку
        """
        if isinstance(text, str):
            return text
        if isinstance(text, list):
            parts = []
            for part in text:
                if isinstance(part, str):
                    parts.append(part)
                elif isinstance(part, dict):
                    parts.append(part.get("text", ""))
            return "".join(parts)
        return ""

    def extract_entities(self, msg: dict) -> tuple[list, list]:
        """
        Вытаскивание упоминаний и ссылок из разметки сообщения
        Возвращает:
            (mentions, links): упоминания вида '@username' / 'user:123' и список URL
        """
        entities = msg.get("text_entities")
        if entities is None and isinstance(msg.get("text"), list):
            entities = [part for part in msg["text"] if isinstance(part, dict)]

        mentions, links = [], []
        for entity in entities or []:
            entity_type = entity.get("type")
            if entity_type == "mention":
                mentions.append(entity.get("text", "").lower())
            elif entity_type == "mention_name":
                if entity.get("user_id"):
                    mentions.append(f"user:{entity['user_id']}")
                mentions.append(entity.get("text", "").lower())
            elif entity_type == "link":
                links.append(entity.get("text", ""))
            elif entity_type == "text_link":
                links.append(entity.get("href", entity.get("text", "")))
        return mentions, links

    def detect_media(self, msg: dict) -> tuple:
        """
        Тип вложения и имя файла
        Возвращает:
            (media_type, file_name), например ('photo', None) или ('file', 'lab3.pdf')
        """
        if "photo" in msg:
            return "photo", None
        if msg.get("media_type"):
            return msg["media_type"], msg.get("file_name")
        if "file" in msg:
            file_name = msg.get("file_name") or os.path.basename(str(msg.get("file", "")))
            return "file", file_name
        if "poll" in msg:
            return "poll", None
        if "location_information" in msg:
            return "location", None
        if "contact_information" in msg:
            return "contact", None
        return None, None

    def count_reactions(self, msg: dict) -> int:
        """
        Суммарное количество реакций на сообщение (есть в новых версиях экспорта)
        """
        return sum(int(r.get("count", 0)) for r in msg.get("reactions", []) or [])

    def parse_message(self, msg: dict, chat_id: int) -> dict:
        """
        Перевод одного сообщения из формата экспорта в формат базы данных
        """
        from_id = msg.get("from_id") or msg.get("actor_id") or ""
        is_mine = bool(self.my_user_id) and from_id == f"user{self.my_user_id}"
        mentions, links = self.extract_entities(msg)
        media_type, file_name = self.detect_media(msg)
        msg_date = datetime.fromisoformat(msg["date"])

        text = self.flatten_text(msg.get("text", ""))
        if not text and msg.get("sticker_emoji"):
            text = msg["sticker_emoji"]
        if msg.get("poll"):
            poll = msg["poll"]
            answers = "; ".join(a.get("text", "") for a in poll.get("answers", []))
            text = f"{text}\n[Опрос] {poll.get('question', '')}: {answers}".strip()

        return {
            "chat_id": chat_id,
            "message_id": msg["id"],
            "sender_name": msg.get("from") or msg.get("author") or "Без имени",
            "sender_id": from_id,
            "msg_date": msg_date,
            "msg_text": text,
            "reply_to": msg.get("reply_to_message_id"),
            "is_mine": is_mine,
            "is_pinned": False,
            "media_type": media_type,
            "file_name": file_name,
            "forwarded_from": msg.get("forwarded_from"),
            "via_bot": msg.get("via_bot"),
            "mentions": mentions,
            "links": links,
            "reactions_count": self.count_reactions(msg),
        }

    def parse_chat(self, chat: dict) -> tuple[dict, list[dict]]:
        """
        Разбор одного чата: информация о чате + список сообщений
        Служебные сообщения (вступил, вышел...) пропускаются,
        но "закрепил сообщение" используется, чтобы пометить закреплённые
        """
        chat_info = {
            "chat_id": chat.get("id"),
            "chat_name": chat.get("name") or ("Избранное" if chat.get("type") == "saved_messages" else "Без названия"),
            "chat_type": chat.get("type", "unknown"),
        }

        pinned_ids = set()
        messages = []
        for msg in chat.get("messages", []):
            if msg.get("type") == "service":
                if msg.get("action") == "pin_message" and msg.get("message_id"):
                    pinned_ids.add(msg["message_id"])
                continue
            if msg.get("type") != "message":
                continue
            messages.append(self.parse_message(msg, chat_info["chat_id"]))

        for message in messages:
            if message["message_id"] in pinned_ids:
                message["is_pinned"] = True
        return chat_info, messages

    def iter_chats(self, data: dict):
        """
        Генератор по всем чатам экспорта (с учётом SKIP_CHAT_TYPES)
        """
        self.detect_my_id(data)
        for chat in self.get_chat_list(data):
            if chat.get("type") in self.skip_chat_types:
                continue
            if chat.get("id") is None:
                continue
            yield self.parse_chat(chat)


def import_export(db, path: str, verbose: bool = True) -> dict:
    """
    Импорт экспорта Telegram в базу данных
    Возвращает:
        dict: статистика импорта (чатов, сообщений всего, новых, мой id)
    """
    parser = Telegram_Export_Parser(config.MY_USER_ID, config.SKIP_CHAT_TYPES)
    data = parser.load(path)
    my_id = parser.detect_my_id(data)

    result = {"chats": 0, "messages": 0, "new": 0, "my_id": my_id}
    for chat_info, messages in parser.iter_chats(data):
        db.insert_chat(chat_info["chat_id"], chat_info["chat_name"], chat_info["chat_type"])
        new_count = db.insert_messages(messages)
        db.set_pinned(chat_info["chat_id"], [m["message_id"] for m in messages if m["is_pinned"]])
        result["chats"] += 1
        result["messages"] += len(messages)
        result["new"] += new_count
        if verbose:
            print(f"  {chat_info['chat_name'][:40]:<40} {len(messages):>7} сообщ. (+{new_count} новых)")
    return result
