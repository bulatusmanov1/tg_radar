import importlib
import os
import sys
from datetime import datetime, timedelta

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

import config
from database import Telegram_DB
from filters import Message_Filters
from tg_import import Telegram_Export_Parser


class TG_Radar:
    def __init__(self):
        """
        Главный класс: связывает импорт, базу данных и фильтры, выполняет команды из терминала
        """
        self.db = Telegram_DB(config.DB_PATH)

    def short(self, text, length: int = 90) -> str:
        """
        Текст в одну строку, обрезанный до length символов
        """
        text = " ".join(str(text or "").split())
        return text if len(text) <= length else text[:length - 1] + "…"

    def parse_day(self, value: str) -> str:
        """
        Дата из ввода пользователя -> "ГГГГ-ММ-ДД".
        Понимает 22.10, 22.10.2026 и 2026-10-22; год по умолчанию — год последнего сообщения в базе
        """
        if "-" in value:
            return datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d")
        parts = value.split(".")
        year = int(parts[2]) if len(parts) == 3 else int(self.db.get_last_date()[:4])
        return datetime(year, int(parts[1]), int(parts[0])).strftime("%Y-%m-%d")

    def print_rows(self, rows, empty_text: str):
        """
        Вывод списка сообщений: (id, дата, место, отправитель, текст, баллы)
        """
        if not rows:
            print(empty_text)
            return
        for row in rows:
            ref, date, place, sender, text, score = row[:6]
            print(f"#{ref:<5} [{score:>2}] {date[:16]}  {self.short(place, 30)} · {self.short(sender, 20)}")
            print(f"        {self.short(text) or '(без текста)'}")

    def score_messages(self, only_new: bool = True) -> int:
        """
        Оценка сообщений фильтрами.
        only_new=True — только что импортированные (у них ещё нет оценки), заодно сохраняются их дедлайны.
        only_new=False — пересчёт всех оценок (rescore); дедлайны не трогаются, чтобы не потерять отметки done
        Возвращает:
            сколько сообщений оценено
        """
        filters = Message_Filters(self.db.get_my_message_keys())
        messages = self.db.get_messages_for_scoring(only_new)
        for msg in messages:
            verdict = filters.score(msg)
            self.db.save_score(msg["id"], verdict["score"], verdict["reasons"], verdict["is_question"])
            if only_new:
                self.db.insert_deadlines(msg["id"], verdict["deadlines"])
        self.db.commit()
        self.db.update_answered()
        return len(messages)

    def cmd_import(self, args):
        """
        Импорт result.json: сообщения -> база -> админы -> оценка новых сообщений
        """
        path = " ".join(args) if args else config.EXPORT_PATH
        if not os.path.exists(path):
            print(f"Файл не найден: {path}")
            return
        if not config.MY_USER_ID:
            print("! MY_USER_ID в config.py не задан: не будут работать 'ответ мне' и 'вопросы без ответа'")

        parser = Telegram_Export_Parser(config.MY_USER_ID)
        for chat_id, name, records, admins in parser.parse(path):
            added = self.db.insert_messages(records)
            self.db.mark_admins(chat_id, admins)
            print(f"  {self.short(name, 40):<40} сообщений: {len(records)}, новых: {added}, админов: {len(admins)}")
        scored = self.score_messages(only_new=True)
        print(f"Оценено новых сообщений: {scored}. Сводка за последний день: day")

    def cmd_day(self, args):
        """
        Главная команда — сводка за день: где сколько писали, что важного, новые дедлайны, кто ждёт ответа.
        day — последний день в базе, day 22.10 — конкретный день
        """
        try:
            day = self.parse_day(args[0]) if args else self.db.get_last_date()
        except (ValueError, IndexError):
            print("Не понял дату. Примеры: day 22.10, day 22.10.2026")
            return
        if day is None:
            print("База пустая, сначала import")
            return
        overview = self.db.get_day_overview(day, config.IMPORTANT_SCORE)
        total = sum(row[1] for row in overview)
        print(f"===== Сводка за {datetime.strptime(day, '%Y-%m-%d'):%d.%m.%Y}: {total} сообщений =====")
        if not overview:
            return

        print("\nГде писали (всего / важных):")
        for place, count, important in overview:
            print(f"  {self.short(place, 40):<40} {count:>4} / {important}")

        print("\nВажное:")
        for ref, date, place, sender, text, score, reasons in self.db.get_important(config.IMPORTANT_SCORE, day, day, limit=10):
            print(f"#{ref:<5} [{score:>2}] {date[11:16]}  {self.short(place, 30)} · {self.short(sender, 20)}")
            print(f"        {self.short(text)}")
            print(f"        почему: {self.short(reasons, 85)}")

        deadlines = self.db.get_deadlines(created_on=day)
        if deadlines:
            print("\nНовые даты из сообщений этого дня:")
            for deadline_id, date, kind, place, text in deadlines:
                label = "дедлайн" if kind == "deadline" else "событие"
                print(f"  {date}  {label:<8} {self.short(place, 25):<25} {self.short(text, 55)}")

        unanswered = self.db.get_unanswered(day)
        if unanswered:
            print("\nЖдут моего ответа:")
            self.print_rows(unanswered, "")

    def cmd_important(self, args):
        """
        Важные сообщения за последние N дней до последнего сообщения в базе (по умолчанию 7)
        """
        days = int(args[0]) if args and args[0].isdigit() else 7
        last = self.db.get_last_date()
        if last is None:
            print("База пустая, сначала import")
            return
        first = (datetime.strptime(last, "%Y-%m-%d") - timedelta(days=days - 1)).strftime("%Y-%m-%d")
        rows = self.db.get_important(config.IMPORTANT_SCORE, first, last)
        self.print_rows(rows, f"За {days} дн. важного нет")

    def cmd_show(self, args):
        """
        Сообщение целиком + почему у него такая оценка + найденные даты
        """
        if not args or not args[0].isdigit():
            print("Пример: show 12")
            return
        row = self.db.get_message(int(args[0]))
        if row is None:
            print("Нет такого сообщения")
            return
        ref, date, chat, topic, sender, text, score, reasons = row
        place = f"{chat} / {topic}" if topic else chat
        print(f"#{ref} {date[:16]}  {place} · {sender}  [{score} баллов]\n")
        print(text or "(без текста)")
        print("\nПочему:")
        for reason in (reasons or "ни один фильтр не сработал").split("; "):
            print(f"  • {reason}")
        for deadline_date, kind, raw in self.db.get_message_deadlines(ref):
            print(f"  📅 {deadline_date} ({kind}): «{raw}»")

    def cmd_deadlines(self, args):
        """
        Невыполненные дедлайны и события начиная с сегодняшнего дня.
        По умолчанию — только из важных сообщений, deadlines all — из всех
        """
        now = datetime.now()
        min_score = None if "all" in args else config.IMPORTANT_SCORE
        rows = self.db.get_deadlines(date_from=now.strftime("%Y-%m-%d 00:00"), min_score=min_score)
        if not rows:
            print("Дедлайнов нет")
            return
        for deadline_id, date, kind, place, text in rows:
            days_left = (datetime.strptime(date, "%Y-%m-%d %H:%M") - now).days
            mark = "🔴" if days_left < 1 else "🟡" if days_left < config.SOON_DAYS else "🟢"
            label = "дедлайн" if kind == "deadline" else "событие"
            print(f"{mark} {deadline_id:<4} {date}  {label:<8} {self.short(place, 25):<25} {self.short(text, 60)}")
        print("Отметить выполненным: done <номер>")

    def cmd_done(self, args):
        """
        Отметить дедлайн выполненным
        """
        if not args or not args[0].isdigit():
            print("Пример: done 4")
        elif self.db.mark_deadline_done(int(args[0])):
            print("Готово ✓")
        else:
            print("Нет такого дедлайна")

    def cmd_unanswered(self, args):
        """
        Вопросы ко мне без ответа за всё время
        """
        self.print_rows(self.db.get_unanswered(), "Все вопросы отвечены")

    def cmd_search(self, args):
        """
        Поиск по тексту сообщений
        """
        if not args:
            print("Пример: search экзамен")
            return
        self.print_rows(self.db.search(" ".join(args)), "Ничего не найдено")

    def cmd_senders(self, args):
        """
        Самые активные участники с id — чтобы выбрать преподавателей для VIP_SENDERS
        """
        print(f"{'имя':<25} {'id':<16} {'сообщ.':>6} {'важных':>6}")
        for sender, sender_id, total, important, is_admin in self.db.get_senders(config.IMPORTANT_SCORE):
            mark = " (админ)" if is_admin else ""
            print(f"{self.short(sender, 25):<25} {sender_id:<16} {total:>6} {important:>6}{mark}")

    def cmd_stats(self, args):
        """
        Четыре графика на одной картинке: активность по дням, темы/чаты, участники, часы.
        Картинка сохраняется в STATS_DIR и сразу открывается
        """
        last = self.db.get_last_date()
        if last is None:
            print("База пустая, сначала import")
            return
        by_day = self.db.get_activity_by_day(config.IMPORTANT_SCORE, last, config.STATS_DAYS)
        places = self.db.get_top_places(config.IMPORTANT_SCORE)[::-1]
        senders = self.db.get_senders(config.IMPORTANT_SCORE, limit=10)[::-1]
        by_hour = dict(self.db.get_activity_by_hour())

        fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(15, 10))
        days = [row[0][5:] for row in by_day]
        ax1.plot(days, [row[1] for row in by_day], marker="o", label="все")
        ax1.plot(days, [row[2] for row in by_day], marker="o", color="crimson", label="важные")
        ax1.set_title(f"Сообщения по дням ({config.STATS_DAYS} дн.)")
        ax1.tick_params(axis="x", rotation=70, labelsize=8)
        ax1.legend()

        names = [self.short(row[0], 30) for row in places]
        ax2.barh(names, [row[1] for row in places], label="все")
        ax2.barh(names, [row[2] for row in places], color="crimson", label="важные")
        ax2.set_title("Темы и чаты")
        ax2.legend()

        names = [self.short(row[0], 20) for row in senders]
        ax3.barh(names, [row[2] for row in senders], label="все")
        ax3.barh(names, [row[3] for row in senders], color="crimson", label="важные")
        ax3.set_title("Самые активные участники")
        ax3.legend()

        ax4.bar(range(24), [by_hour.get(hour, 0) for hour in range(24)])
        ax4.set_xticks(range(24))
        ax4.set_title("Активность по часам")

        os.makedirs(config.STATS_DIR, exist_ok=True)
        path = os.path.join(config.STATS_DIR, f"stats_{datetime.now():%Y%m%d_%H%M%S}.png")
        fig.tight_layout()
        fig.savefig(path, dpi=110)
        plt.close(fig)
        print(f"График сохранён: {path}")
        if hasattr(os, "startfile"):
            os.startfile(path)

    def cmd_rescore(self, args):
        """
        Перечитать config.py и пересчитать оценки всех сообщений — без перезапуска программы
        """
        importlib.reload(config)
        count = self.score_messages(only_new=False)
        print(f"Пересчитано сообщений: {count}")

    def cmd_help(self, args):
        """
        Список команд
        """
        for name, (_, description) in self.get_commands().items():
            print(f"  {name:<11} {description}")

    def get_commands(self) -> dict:
        """
        Команда -> (функция, описание)
        """
        return {
            "import": (self.cmd_import, "import [путь] — загрузить result.json (по умолчанию EXPORT_PATH)"),
            "day": (self.cmd_day, "day [22.10] — сводка за день (по умолчанию последний день)"),
            "important": (self.cmd_important, "important [N] — важное за N дней (по умолчанию 7)"),
            "show": (self.cmd_show, "show <id> — сообщение целиком и почему оно важное"),
            "deadlines": (self.cmd_deadlines, "ближайшие дедлайны из важных сообщений (deadlines all — из всех)"),
            "done": (self.cmd_done, "done <номер> — дедлайн выполнен"),
            "unanswered": (self.cmd_unanswered, "вопросы ко мне без ответа"),
            "search": (self.cmd_search, "search <текст> — поиск"),
            "senders": (self.cmd_senders, "самые активные участники с id (для VIP_SENDERS)"),
            "stats": (self.cmd_stats, "графики статистики"),
            "rescore": (self.cmd_rescore, "перечитать config.py и пересчитать оценки"),
            "help": (self.cmd_help, "список команд"),
            "exit": (None, "выход"),
        }

    def run(self):
        """
        Главный цикл
        """
        commands = self.get_commands()
        count, chats, first, last = self.db.get_totals()
        if count:
            print(f"В базе {count} сообщений из {chats} чат(ов), с {first[:10]} по {last[:10]}")
        else:
            print("База пустая. Загрузите экспорт: import result.json")
        print("help — список команд, exit — выход\n")

        while True:
            try:
                line = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                continue
            name, *args = line.split()
            if name == "exit":
                break
            if name not in commands:
                print("Неизвестная команда, help — список команд")
                continue
            try:
                commands[name][0](args)
            except Exception as e:
                print(f"! Ошибка: {e}")
            print()


def main():
    app = TG_Radar()
    if not app.db.tables_exist():
        print(f"В {config.DB_PATH} нет таблиц. Сначала выполните: python init_db.py")
        sys.exit(1)
    try:
        app.run()
    finally:
        app.db.close()


if __name__ == "__main__":
    main()
