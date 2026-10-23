import config
from database import Telegram_DB


def init_database():
    """
    Создание таблиц в файле config.DB_PATH.
    """
    db = Telegram_DB(config.DB_PATH)
    if db.tables_exist():
        answer = input(f"В {config.DB_PATH} уже есть данные. Удалить их и создать таблицы заново? [y/N] ")
        if answer.strip().lower() not in ("y", "yes", "д", "да"):
            print("Отменено")
            db.close()
            return
    db.init_all()
    db.close()
    print(f"Таблицы messages и deadlines созданы в {config.DB_PATH}")


if __name__ == "__main__":
    init_database()
