import sys

import psycopg2

import config
from database import Telegram_DB


def create_database_if_missing():
    """
    Создание самой базы данных (DB_SETTINGS['dbname']), если её ещё нет.
    Подключается к служебной базе postgres и выполняет CREATE DATABASE
    """
    settings = dict(config.DB_SETTINGS)
    dbname = settings.pop("dbname")
    conn = psycopg2.connect(dbname="postgres", **settings)
    conn.autocommit = True
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s;", (dbname,))
    if cursor.fetchone() is None:
        cursor.execute(f'CREATE DATABASE "{dbname}";')
        print(f"База данных {dbname} создана")
    cursor.close()
    conn.close()


def init_database(force: bool = False) -> bool:
    """
    Создание всех таблиц.
    Если таблицы уже есть — спрашивает подтверждение (все данные будут удалены),
    force=True пропускает вопрос
    """
    create_database_if_missing()
    db = Telegram_DB(**config.DB_SETTINGS)
    try:
        if db.tables_exist() and not force:
            answer = input("Таблицы уже существуют. Удалить все данные и создать заново? [y/N] ")
            if answer.strip().lower() not in ("y", "yes", "д", "да"):
                print("Отменено")
                return False
        db.init_all()
        print("Таблицы chats, messages, analysis, deadlines, rules созданы")
        return True
    finally:
        db.close()


if __name__ == "__main__":
    init_database(force="--force" in sys.argv)
