import argparse
import shlex
import sys
from datetime import datetime

import psycopg2

import config
from database import Telegram_DB
from filters import Importance_Scorer, run_analysis
from init_db import init_database
from notifier import Notifier
from stats import Stats_Plotter
from tg_import import import_export
from ui import Terminal_UI


class TG_Radar_App:
    MENU = [
        ("1", "digest", "сводка: что важного, дедлайны, вопросы без ответа"),
        ("2", "inbox", "важные непрочитанные сообщения"),
        ("3", "deadlines", "ближайшие дедлайны"),
        ("4", "unanswered", "вопросы ко мне без ответа"),
        ("5", "mentions", "упоминания и ответы мне"),
        ("6", "chats", "список чатов"),
        ("7", "stats", "статистика"),
        ("8", "import", "импорт экспорта Telegram"),
        ("9", "analyze", "оценить новые сообщения"),
    ]

    def __init__(self):
        """
        Приложение TG Radar: связывает базу, фильтры и вывод в терминал
        """
        self.ui = Terminal_UI()
        self.db = None

    def connect(self):
        """
        Подключение к базе (лениво, при первой команде, которой она нужна)
        """
        if self.db is None:
            try:
                self.db = Telegram_DB(**config.DB_SETTINGS)
            except psycopg2.OperationalError as e:
                self.ui.error("Не удалось подключиться к PostgreSQL. Проверьте DB_SETTINGS в config.py")
                self.ui.error(str(e).strip().splitlines()[0] if str(e).strip() else "нет подробностей")
                self.ui.info("Если базы ещё нет — запустите: python main.py init")
                sys.exit(1)
            if not self.db.tables_exist():
                self.ui.warn("Таблицы не созданы. Запустите: python main.py init")
                sys.exit(1)
        return self.db

    def resolve_chat(self, query: str):
        """
        Поиск чата по id или части названия. Если подходят несколько — показывает список
        Возвращает:
            chat_id или None
        """
        if query is None:
            return None
        found = self.connect().find_chat(query)
        if not found:
            self.ui.error(f"Чат «{query}» не найден")
            return None
        exact = [row for row in found if str(row[1]).lower() == query.lower()]
        if len(found) == 1 or len(exact) == 1:
            return (exact or found)[0][0]
        self.ui.warn("Подходит несколько чатов, уточните (можно по id):")
        self.ui.table(["chat_id", "название"], found[:15], "rl")
        return None

    def cmd_init(self, args):
        """
        Создание базы и таблиц
        """
        init_database(force=args.force)

    def cmd_import(self, args):
        """
        Импорт result.json из Telegram Desktop + сразу оценка важности
        """
        db = self.connect()
        path = args.path or config.EXPORT_PATH
        self.ui.title(f"Импорт: {path}")
        try:
            result = import_export(db, path)
        except FileNotFoundError as e:
            self.ui.error(str(e))
            self.ui.info("Telegram Desktop -> Настройки -> Продвинутые -> Экспорт данных, формат JSON")
            return
        self.ui.ok(f"Чатов: {result['chats']}, сообщений: {result['messages']}, новых: {result['new']}")
        if result["my_id"]:
            self.ui.info(f"Мой user_id: {result['my_id']}")
        else:
            self.ui.warn("Не удалось определить мой user_id — впишите MY_USER_ID в config.py, "
                         "иначе не будут работать 'ответ мне' и 'вопрос без ответа'")
        if not args.no_analyze:
            self.analyze(reanalyze=False, my_user_id=result["my_id"])

    def analyze(self, reanalyze: bool, my_user_id=None):
        """
        Запуск оценки важности
        """
        self.ui.title("Оценка важности" + (" (заново)" if reanalyze else ""))
        totals = run_analysis(self.connect(), reanalyze=reanalyze, my_user_id=my_user_id)
        self.ui.ok(
            f"critical: {totals['critical']}, high: {totals['high']}, medium: {totals['medium']}, "
            f"low: {totals['low']}, мои: {totals['mine']}, дат/дедлайнов найдено: {totals['deadlines']}"
        )

    def cmd_analyze(self, args):
        """
        Оценка новых сообщений (или всех заново с --reanalyze после смены весов)
        """
        if args.reanalyze:
            answer = self.ui.ask("Отметки 'прочитано' и 'сделано' будут сброшены. Продолжить? [y/N] ", "n")
            if answer.lower() not in ("y", "yes", "д", "да"):
                return
        self.analyze(args.reanalyze)

    def cmd_inbox(self, args):
        """
        Важные непрочитанные сообщения
        """
        levels = ["critical", "high"]
        if args.medium:
            levels.append("medium")
        if args.level:
            levels = [args.level]
        chat_id = self.resolve_chat(args.chat) if args.chat else None
        if args.chat and chat_id is None:
            return

        rows = self.connect().get_inbox(levels, args.limit, args.read, args.days, chat_id)
        self.ui.title(f"Входящие ({', '.join(levels)})")
        if not rows:
            self.ui.ok("Ничего важного непрочитанного 🎉")
            return
        for ref, chat, sender, date, text, score, level, reasons, is_read, media in rows:
            self.ui.message_card(ref, chat, sender, date, text, score, level,
                                 reasons if args.why else None, media, full=args.full)
        print()
        self.ui.info("show <id> — подробнее, read <id> [<id>...] — прочитано, read --all — всё прочитано")

    def cmd_show(self, args):
        """
        Подробности сообщения: полный текст, почему оно важное, контекст переписки
        """
        db = self.connect()
        details = db.get_message_details(args.id)
        if details is None:
            self.ui.error(f"Сообщение #{args.id} не найдено")
            return

        self.ui.title(f"Сообщение #{details['id']}")
        self.ui.message_card(details["id"], details["chat_name"], details["sender_name"], details["msg_date"],
                             details["msg_text"], details["score"], details["level"], None,
                             details["media_type"], full=True)
        if details["file_name"]:
            self.ui.info(f"Файл: {details['file_name']}")
        if details["links"]:
            self.ui.info("Ссылки: " + ", ".join(details["links"][:5]))

        print()
        print(self.ui.c("Почему такая оценка:", "bold"))
        for reason in details["reasons"] or ["ни один фильтр не сработал"]:
            print("  • " + reason)

        if details["deadlines"]:
            print(self.ui.c("Даты в сообщении:", "bold"))
            for date, kind, raw in details["deadlines"]:
                print(f"  • {date:%d.%m.%Y %H:%M}  [{kind}]  «{raw}»")

        if args.context:
            print()
            print(self.ui.c("Контекст:", "bold"))
            for ref, sender, date, text in db.get_context(args.id, args.context):
                marker = self.ui.c("→", "yellow") if ref == args.id else " "
                print(f" {marker} {date:%d.%m %H:%M} {self.ui.short(sender, 18):<18} {self.ui.short(text, self.ui.width() - 34)}")

        if not details["is_read"] and details["level"] not in (None, "mine"):
            db.mark_read([args.id])

    def cmd_read(self, args):
        """
        Пометить прочитанным
        """
        db = self.connect()
        if args.all:
            count = db.mark_all_read()
        elif args.ids:
            count = db.mark_read(args.ids)
        else:
            self.ui.warn("Укажите id сообщений или --all")
            return
        self.ui.ok(f"Помечено прочитанными: {count}")

    def deadline_color(self, date: datetime) -> str:
        """
        Цвет дедлайна по близости
        """
        hours = (date - datetime.now()).total_seconds() / 3600
        if hours < 0:
            return "gray"
        if hours <= 24:
            return "red"
        if hours <= 72:
            return "yellow"
        return "green"

    def human_delta(self, date: datetime) -> str:
        """
        "через 2 дн. 5 ч." / "3 ч. назад"
        """
        seconds = (date - datetime.now()).total_seconds()
        future = seconds >= 0
        seconds = abs(seconds)
        days, hours = int(seconds // 86400), int(seconds % 86400 // 3600)
        if days:
            text = f"{days} дн. {hours} ч."
        elif hours:
            text = f"{hours} ч."
        else:
            text = f"{int(seconds // 60)} мин."
        return f"через {text}" if future else f"{text} назад"

    def cmd_deadlines(self, args):
        """
        Дедлайны и события из сообщений
        """
        kind = None if args.events else "deadline"
        rows = self.connect().get_deadlines(args.days, args.all, args.past, kind)
        self.ui.title("Дедлайны" + (" и события" if args.events else ""))
        if not rows:
            self.ui.ok("Ближайших дедлайнов не найдено")
            if not args.past:
                self.ui.info("Старый экспорт? Попробуйте: deadlines --past")
            return
        for dl_id, date, kind_value, raw, done, chat, sender, text, ref in rows:
            status = self.ui.c("✓", "green") if done else self.ui.c("●", self.deadline_color(date))
            when = self.ui.c(f"{date:%d.%m %H:%M}", self.deadline_color(date))
            delta = self.ui.c(f"{self.human_delta(date):<17}", "gray")
            label = self.ui.c(f"{'дедлайн' if kind_value == 'deadline' else 'событие':<8}", "cyan" if kind_value == "event" else "gray")
            chat_cell = self.ui.c(self.ui.short(chat, 22).ljust(22), "magenta")
            print(f" {status} {dl_id:>4}  {when}  {delta} {label} {chat_cell}  "
                  f"{self.ui.short(text, max(20, self.ui.width() - 78))}  {self.ui.c(f'(#{ref})', 'gray')}")
        print()
        self.ui.info("done <номер> — отметить выполненным")

    def cmd_done(self, args):
        """
        Отметить дедлайн выполненным
        """
        if self.connect().mark_deadline_done(args.id, not args.undo):
            self.ui.ok(f"Дедлайн {args.id}: {'снова активен' if args.undo else 'выполнен'}")
        else:
            self.ui.error(f"Дедлайн {args.id} не найден")

    def print_message_rows(self, rows, empty_text: str):
        """
        Вывод списка сообщений (id, чат, отправитель, дата, текст, баллы)
        """
        if not rows:
            self.ui.ok(empty_text)
            return
        for ref, chat, sender, date, text, score, *rest in rows:
            self.ui.message_card(ref, chat, sender, date, text, score, rest[0] if rest else None)

    def cmd_unanswered(self, args):
        """
        Вопросы ко мне, на которые я не ответил
        """
        self.ui.title("Вопросы без ответа")
        self.print_message_rows(self.connect().get_unanswered(args.limit), "Все вопросы отвечены")

    def cmd_mentions(self, args):
        """
        Упоминания и ответы мне
        """
        self.ui.title("Упоминания и ответы мне")
        self.print_message_rows(self.connect().get_mentions(args.limit), "Упоминаний нет")

    def cmd_search(self, args):
        """
        Поиск по тексту
        """
        chat_id = self.resolve_chat(args.chat) if args.chat else None
        if args.chat and chat_id is None:
            return
        text = " ".join(args.text)
        self.ui.title(f"Поиск: {text}")
        self.print_message_rows(self.connect().search_messages(text, chat_id, args.limit), "Ничего не найдено")

    def cmd_chats(self, args):
        """
        Список чатов
        """
        rows = self.connect().get_chats()
        if args.filter:
            rows = [row for row in rows if args.filter.lower() in str(row[1]).lower()]
        self.ui.title("Чаты")
        table = []
        for chat_id, name, chat_type, priority, muted, total, important, last in rows[:args.limit]:
            flags = ("🔇" if muted else "") + (f"★{priority}" if priority > 0 else (f"↓{priority}" if priority < 0 else ""))
            table.append([chat_id, name, chat_type, total, important, f"{last:%d.%m.%Y}" if last else "-", flags])
        self.ui.table(["chat_id", "название", "тип", "сообщ.", "важных", "последнее", ""], table, "rllrrll")
        print()
        self.ui.info("mute <чат>, unmute <чат>, priority <чат> <число> — после изменения: analyze --reanalyze")

    def cmd_mute(self, args):
        """
        Заглушить / включить чат
        """
        chat_id = self.resolve_chat(" ".join(args.chat))
        if chat_id is not None and self.connect().set_chat_muted(chat_id, not args.unmute):
            self.ui.ok(f"Чат {chat_id} {'включён' if args.unmute else 'заглушён'}")

    def cmd_priority(self, args):
        """
        Приоритет чата
        """
        chat_id = self.resolve_chat(" ".join(args.chat))
        if chat_id is not None and self.connect().set_chat_priority(chat_id, args.value):
            self.ui.ok(f"Приоритет чата {chat_id}: {args.value}")

    def cmd_rules(self, args):
        """
        Пользовательские правила: ключевые слова, VIP-отправители, VIP-чаты
        """
        db = self.connect()
        if args.action == "add":
            if not args.type or not args.value:
                self.ui.error("Пример: rules add keyword 'курсов\\w*' --weight 3")
                return
            db.add_rule(args.type, " ".join(args.value), args.weight)
            self.ui.ok("Правило добавлено. Чтобы применить к старым сообщениям: analyze --reanalyze")
        elif args.action == "remove":
            if not args.value or not args.value[0].isdigit():
                self.ui.error("Пример: rules remove 3")
                return
            if db.remove_rule(int(args.value[0])):
                self.ui.ok("Правило удалено")
            else:
                self.ui.error("Правило не найдено")
        self.ui.title("Правила")
        rows = db.get_rules()
        if rows:
            self.ui.table(["id", "тип", "значение", "вес"], rows, "rllr")
        else:
            self.ui.info("Своих правил пока нет (базовые — в config.py)")

    def cmd_stats(self, args):
        """
        Статистика в терминале (+ PNG-графики с --plot)
        """
        db = self.connect()
        totals = db.get_totals()
        self.ui.title("Статистика")
        if totals["first"]:
            self.ui.info(f"Период: {totals['first']:%d.%m.%Y} — {totals['last']:%d.%m.%Y}")
        self.ui.info(f"Чатов: {totals['chats']}, сообщений: {totals['messages']} (моих: {totals['mine']})")

        activity = db.get_activity_by_day(args.days)
        if activity:
            print(f"\n  Активность за {args.days} дн.: {self.ui.c(self.ui.sparkline([row[1] for row in activity]), 'cyan')}")

        print(self.ui.c("\nУровни важности", "bold"))
        self.ui.bar_chart(db.get_level_distribution(), "yellow")

        print(self.ui.c("\nСамые активные чаты", "bold"))
        self.ui.bar_chart([(row[0], row[1]) for row in db.get_top_chats(args.limit)])

        print(self.ui.c("\nГде больше всего важного", "bold"))
        self.ui.bar_chart([(row[0], row[2]) for row in db.get_top_chats(args.limit, by="important") if row[2]], "red")

        print(self.ui.c("\nКто пишет больше всех", "bold"))
        self.ui.bar_chart([(row[0], row[1]) for row in db.get_top_senders(args.limit)], "magenta")

        print(self.ui.c("\nАктивность по часам", "bold"))
        self.ui.bar_chart([(f"{hour:02d}:00", count) for hour, count in db.get_activity_by_hour()], "blue")

        weekdays = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
        print(self.ui.c("\nПо дням недели", "bold"))
        self.ui.bar_chart([(weekdays[dow - 1], count) for dow, count in db.get_activity_by_weekday()], "green")

        print(self.ui.c("\nКакие фильтры срабатывают чаще (для настройки весов)", "bold"))
        self.ui.bar_chart(db.get_reason_stats(15), "gray")

        response = db.get_response_time_stats(args.limit)
        if response:
            print(self.ui.c("\nМоё среднее время ответа в личках, мин", "bold"))
            self.ui.bar_chart([(row[0], float(row[1])) for row in response], "yellow", value_width=8)

        if args.plot:
            paths = Stats_Plotter(db, config.STATS_DIR).plot_all(args.days)
            print()
            for path in paths:
                self.ui.ok(f"График: {path}")

    def cmd_digest(self, args):
        """
        Сводка: главное за раз
        """
        db = self.connect()
        totals = db.get_totals()
        self.ui.title(f"TG Radar · {datetime.now():%d.%m.%Y %H:%M}")
        print(f"  {self.ui.c(totals['critical'], 'red')} критичных · {self.ui.c(totals['high'], 'yellow')} важных · "
              f"{self.ui.c(totals['deadlines'], 'cyan')} предстоящих дедлайнов/событий · "
              f"сообщений в базе: {totals['messages']}")

        rows = db.get_inbox(["critical", "high"], args.limit)
        if rows:
            print(self.ui.c("\nГлавное:", "bold"))
            for ref, chat, sender, date, text, score, level, reasons, is_read, media in rows:
                self.ui.message_card(ref, chat, sender, date, text, score, level, None, media)

        deadlines = db.get_deadlines(days=7)
        if deadlines:
            print(self.ui.c("\nДедлайны на неделю:", "bold"))
            for dl_id, date, kind, raw, done, chat, sender, text, ref in deadlines[:args.limit]:
                print(f"  {self.ui.c(f'{date:%d.%m %H:%M}', self.deadline_color(date))}  "
                      f"{self.ui.c(self.ui.short(chat, 22).ljust(22), 'magenta')}  {self.ui.short(text, self.ui.width() - 45)}")

        unanswered = db.get_unanswered(args.limit)
        if unanswered:
            print(self.ui.c(f"\nЖдут ответа ({len(unanswered)}):", "bold"))
            for ref, chat, sender, date, text, score in unanswered[:args.limit]:
                print(f"  #{ref:<6} {self.ui.c(self.ui.short(sender, 20), 'magenta')}: {self.ui.short(text, self.ui.width() - 35)}")

        if not rows and not deadlines and not unanswered:
            self.ui.ok("Всё спокойно — можно не заходить в телеграм 😌")

    def cmd_test(self, args):
        """
        Проверить оценку на произвольном тексте (без базы)
        """
        from filters import Message_Filters
        text = " ".join(args.text)
        chat_type = "personal_chat" if args.personal else "private_supergroup"
        msg = {
            "id": 0, "chat_id": 0, "message_id": 0, "sender_name": args.sender, "sender_id": "",
            "msg_date": datetime.now(), "msg_text": text, "reply_to": None, "is_mine": False,
            "is_pinned": args.pinned, "media_type": None, "file_name": None, "forwarded_from": None,
            "via_bot": None, "mentions": [w.lower() for w in text.split() if w.startswith("@")],
            "links": [], "reactions_count": 0,
        }
        context = {"chat_info": {0: {"name": "тест", "type": chat_type, "priority": 0, "is_muted": False}}}
        verdict = Importance_Scorer(Message_Filters(config.FILTER_WEIGHTS, context)).score_message(msg)
        self.ui.title("Тест оценки")
        print(f"  {self.ui.level_badge(verdict['level'])} баллы: {verdict['score']}")
        for reason in verdict["reasons"] or ["ни один фильтр не сработал"]:
            print("  • " + reason)
        for item in verdict["deadlines"]:
            print(f"  📅 {item['date']:%d.%m.%Y %H:%M} [{item['kind']}] «{item['raw']}»")

    def cmd_sync(self, args):
        """
        Подтянуть последние сообщения через Telegram API
        """
        from live import Telegram_Live
        try:
            Telegram_Live(self.connect(), self.ui, Notifier()).run_sync(args.dialogs, args.messages)
        except RuntimeError as e:
            self.ui.error(str(e))

    def cmd_live(self, args):
        """
        Слушать новые сообщения и пинать
        """
        from live import Telegram_Live
        try:
            Telegram_Live(self.connect(), self.ui, Notifier()).run_live()
        except RuntimeError as e:
            self.ui.error(str(e))

    def build_parser(self) -> argparse.ArgumentParser:
        """
        Описание всех команд
        """
        parser = argparse.ArgumentParser(
            prog="tg_radar",
            description="TG Radar — находит важное в ваших Telegram-чатах по правилам (без LLM)",
        )
        sub = parser.add_subparsers(dest="command")

        p = sub.add_parser("init", help="создать базу и таблицы")
        p.add_argument("--force", action="store_true", help="не спрашивать подтверждение")
        p.set_defaults(func=self.cmd_init)

        p = sub.add_parser("import", help="импорт result.json из Telegram Desktop")
        p.add_argument("path", nargs="?", help="путь к result.json или папке экспорта")
        p.add_argument("--no-analyze", action="store_true", help="не запускать оценку после импорта")
        p.set_defaults(func=self.cmd_import)

        p = sub.add_parser("analyze", help="оценить важность новых сообщений")
        p.add_argument("--reanalyze", action="store_true", help="пересчитать всё заново (после смены весов)")
        p.set_defaults(func=self.cmd_analyze)

        p = sub.add_parser("inbox", help="важные непрочитанные")
        p.add_argument("-l", "--limit", type=int, default=15)
        p.add_argument("--medium", action="store_true", help="показывать и medium")
        p.add_argument("--level", choices=["critical", "high", "medium", "low"])
        p.add_argument("--read", action="store_true", help="включая прочитанные")
        p.add_argument("--days", type=int, help="только за последние N дней")
        p.add_argument("--chat", help="только один чат (id или часть названия)")
        p.add_argument("--why", action="store_true", help="показать причины")
        p.add_argument("--full", action="store_true", help="полный текст")
        p.set_defaults(func=self.cmd_inbox)

        p = sub.add_parser("show", help="подробности сообщения")
        p.add_argument("id", type=int)
        p.add_argument("-c", "--context", type=int, default=3, help="сколько соседних сообщений показать")
        p.set_defaults(func=self.cmd_show)

        p = sub.add_parser("read", help="пометить прочитанным")
        p.add_argument("ids", nargs="*", type=int)
        p.add_argument("--all", action="store_true")
        p.set_defaults(func=self.cmd_read)

        p = sub.add_parser("deadlines", help="дедлайны")
        p.add_argument("--days", type=int, help="горизонт в днях")
        p.add_argument("--all", action="store_true", help="включая выполненные")
        p.add_argument("--past", action="store_true", help="включая прошедшие")
        p.add_argument("--events", action="store_true", help="показывать и события (не только дедлайны)")
        p.set_defaults(func=self.cmd_deadlines)

        p = sub.add_parser("done", help="отметить дедлайн выполненным")
        p.add_argument("id", type=int)
        p.add_argument("--undo", action="store_true")
        p.set_defaults(func=self.cmd_done)

        p = sub.add_parser("unanswered", help="вопросы ко мне без ответа")
        p.add_argument("-l", "--limit", type=int, default=20)
        p.set_defaults(func=self.cmd_unanswered)

        p = sub.add_parser("mentions", help="упоминания и ответы мне")
        p.add_argument("-l", "--limit", type=int, default=20)
        p.set_defaults(func=self.cmd_mentions)

        p = sub.add_parser("search", help="поиск по тексту")
        p.add_argument("text", nargs="+")
        p.add_argument("--chat")
        p.add_argument("-l", "--limit", type=int, default=20)
        p.set_defaults(func=self.cmd_search)

        p = sub.add_parser("chats", help="список чатов")
        p.add_argument("filter", nargs="?")
        p.add_argument("-l", "--limit", type=int, default=50)
        p.set_defaults(func=self.cmd_chats)

        p = sub.add_parser("mute", help="заглушить чат")
        p.add_argument("chat", nargs="+")
        p.set_defaults(func=self.cmd_mute, unmute=False)

        p = sub.add_parser("unmute", help="включить чат")
        p.add_argument("chat", nargs="+")
        p.set_defaults(func=self.cmd_mute, unmute=True)

        p = sub.add_parser("priority", help="приоритет чата (-5..5)")
        p.add_argument("chat", nargs="+")
        p.add_argument("value", type=int)
        p.set_defaults(func=self.cmd_priority)

        p = sub.add_parser("rules", help="свои правила: rules [list|add|remove]")
        p.add_argument("action", nargs="?", default="list", choices=["list", "add", "remove"])
        p.add_argument("type", nargs="?", help="keyword / sender / chat (для add) или id (для remove)")
        p.add_argument("value", nargs="*")
        p.add_argument("-w", "--weight", type=int, default=2)
        p.set_defaults(func=self.cmd_rules)

        p = sub.add_parser("stats", help="статистика")
        p.add_argument("--plot", action="store_true", help="сохранить PNG-графики")
        p.add_argument("--days", type=int, default=30)
        p.add_argument("-l", "--limit", type=int, default=10)
        p.set_defaults(func=self.cmd_stats)

        p = sub.add_parser("digest", help="сводка")
        p.add_argument("-l", "--limit", type=int, default=7)
        p.set_defaults(func=self.cmd_digest)

        p = sub.add_parser("test", help="проверить оценку на своём тексте")
        p.add_argument("text", nargs="+")
        p.add_argument("--personal", action="store_true", help="как будто это личка")
        p.add_argument("--pinned", action="store_true")
        p.add_argument("--sender", default="Тест")
        p.set_defaults(func=self.cmd_test)

        p = sub.add_parser("sync", help="подтянуть свежие сообщения через Telegram API (telethon)")
        p.add_argument("--dialogs", type=int, default=50)
        p.add_argument("--messages", type=int, default=200)
        p.set_defaults(func=self.cmd_sync)

        p = sub.add_parser("live", help="слушать новые сообщения и пинать (telethon)")
        p.set_defaults(func=self.cmd_live)

        return parser

    def fix_rules_args(self, args):
        """
        Для 'rules remove 3' число попадает в type — переносим его в value
        """
        if getattr(args, "func", None) == self.cmd_rules and args.action == "remove" and args.type:
            args.value = [args.type] + list(args.value)
        return args

    def run_command(self, argv: list[str]) -> bool:
        """
        Выполнение одной команды
        Возвращает:
            bool: False, если команду не удалось разобрать
        """
        parser = self.build_parser()
        try:
            args = parser.parse_args(argv)
        except SystemExit:
            return False
        if not getattr(args, "func", None):
            parser.print_help()
            return False
        self.fix_rules_args(args)
        try:
            args.func(args)
        except psycopg2.Error as e:
            self.ui.error(f"Ошибка базы данных: {e}")
        return True

    def interactive(self):
        """
        Интерактивный режим: меню с цифрами + можно вводить любые команды как в CLI
        """
        self.ui.title("TG Radar")
        print("  Цифра — пункт меню, или любая команда: inbox --why, show 42, search экзамен, help, q — выход")
        while True:
            print()
            for key, command, label in self.MENU:
                print(f"  {self.ui.c(key, 'cyan')} {command:<11} {self.ui.c(label, 'gray')}")
            line = self.ui.ask("\ntg_radar> ")
            if line.lower() in ("q", "quit", "exit", "выход"):
                break
            if not line:
                continue
            if line in ("help", "h", "?"):
                self.build_parser().print_help()
                continue
            for key, command, _ in self.MENU:
                if line == key:
                    line = command
                    break
            try:
                argv = shlex.split(line)
            except ValueError as e:
                self.ui.error(f"Не разобрал команду: {e}")
                continue
            self.run_command(argv)

    def close(self):
        """
        Закрытие соединения
        """
        if self.db is not None:
            self.db.close()


def main():
    """
    Точка входа: без аргументов — интерактивный режим, с аргументами — одна команда
    """
    app = TG_Radar_App()
    try:
        if len(sys.argv) > 1:
            app.run_command(sys.argv[1:])
        else:
            app.interactive()
    except KeyboardInterrupt:
        print()
    finally:
        app.close()


if __name__ == "__main__":
    main()
