import json
import platform
import shutil
import subprocess
import urllib.parse
import urllib.request

import config

LEVEL_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


class Notifier:
    def __init__(self, bot_token: str = None, bot_chat_id: str = None, min_level: str = None, desktop: bool = True):
        """
        "Пинки": звуковой сигнал в терминале, системное уведомление и (опционально) сообщение от бота
        """
        self.bot_token = bot_token if bot_token is not None else config.BOT_TOKEN
        self.bot_chat_id = bot_chat_id if bot_chat_id is not None else config.BOT_CHAT_ID
        self.min_level = min_level or config.NOTIFY_MIN_LEVEL
        self.desktop = desktop

    def should_notify(self, level: str) -> bool:
        """
        Достаточно ли важно сообщение для уведомления
        """
        return LEVEL_ORDER.get(level, -1) >= LEVEL_ORDER.get(self.min_level, 2)

    def format_text(self, chat_name: str, sender: str, text: str, score: int, level: str, reasons: list) -> str:
        """
        Текст уведомления
        """
        icon = "🔴" if level == "critical" else "🟠"
        body = (text or "").strip()
        if len(body) > 500:
            body = body[:500] + "…"
        why = "\n".join(f"• {reason}" for reason in reasons[:6])
        return f"{icon} {level.upper()} [{score}] — {chat_name}\nОт: {sender}\n\n{body}\n\nПочему:\n{why}"

    def bell(self):
        """
        Звуковой сигнал терминала
        """
        print("\a", end="", flush=True)

    def desktop_notify(self, title: str, text: str):
        """
        Системное уведомление: notify-send (Linux) или osascript (macOS)
        """
        if not self.desktop:
            return
        try:
            system = platform.system()
            if system == "Linux" and shutil.which("notify-send"):
                subprocess.run(["notify-send", title, text[:200]], timeout=5, check=False)
            elif system == "Darwin":
                safe_text = text[:200].replace('"', "'")
                safe_title = title.replace('"', "'")
                subprocess.run(
                    ["osascript", "-e", f'display notification "{safe_text}" with title "{safe_title}"'],
                    timeout=5, check=False
                )
        except Exception:
            pass

    def send_bot(self, text: str) -> bool:
        """
        Отправка сообщения через Telegram Bot API (без сторонних библиотек)
        Возвращает:
            bool: True если отправлено
        """
        if not self.bot_token or not self.bot_chat_id:
            return False
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": self.bot_chat_id, "text": text[:4000]}).encode()
        try:
            with urllib.request.urlopen(url, data=data, timeout=10) as response:
                return json.loads(response.read()).get("ok", False)
        except Exception as e:
            print(f"Не удалось отправить через бота: {e}")
            return False

    def notify(self, chat_name, sender, text, score, level, reasons) -> str:
        """
        Полный "пинок": сигнал + системное уведомление + бот
        Возвращает:
            str: текст уведомления (его же можно отправить в Избранное)
        """
        message = self.format_text(chat_name, sender, text, score, level, reasons)
        self.bell()
        self.desktop_notify(f"TG Radar: {level} — {chat_name}", (text or "")[:200])
        self.send_bot(message)
        return message
