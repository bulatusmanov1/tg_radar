import sqlite3

import config
from database import Telegram_DB


YES_ANSWERS = ("y", "yes", "д", "да")


def init_database():
    """
    Создание таблиц в файле config.DB_PATH.
    """
    try:
        db = Telegram_DB(config.DB_PATH)
    except sqlite3.Error as e:
        print(f"Не удалось открыть базу {config.DB_PATH}: {e}")
        return
    try:
        if db.tables_exist():
            answer = input(f"В {config.DB_PATH} уже есть данные. Удалить их и создать таблицы заново? [y/N] ")
            if answer.strip().lower() not in YES_ANSWERS:
                print("Отменено")
                return
        db.init_all()
        print(f"Таблицы messages и deadlines созданы в {config.DB_PATH}")
    except (EOFError, KeyboardInterrupt):
        print("\nОтменено")
    except sqlite3.Error as e:
        print(f"Ошибка базы данных: {e}")
    finally:
        db.close()


if __name__ == "__main__":
    init_database()
