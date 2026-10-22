import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


class Stats_Plotter:
    def __init__(self, db, out_dir: str = "stats"):
        """
        Построение графиков статистики в PNG (matplotlib, без окон — просто файлы)
        """
        self.db = db
        self.out_dir = out_dir
        os.makedirs(out_dir, exist_ok=True)

    def _save(self, fig, name: str) -> str:
        """
        Сохранение графика в файл
        """
        path = os.path.join(self.out_dir, name)
        fig.tight_layout()
        fig.savefig(path, dpi=120)
        plt.close(fig)
        return path

    def plot_activity_by_day(self, days: int = 30, chat_id=None) -> str:
        """
        Линейный график: сообщения по дням (все / мои / важные)
        """
        rows = self.db.get_activity_by_day(days, chat_id)
        if not rows:
            return None
        dates = [row[0] for row in rows]
        fig, ax = plt.subplots(figsize=(11, 4.5))
        ax.plot(dates, [row[1] for row in rows], label="все", linewidth=2)
        ax.plot(dates, [row[2] for row in rows], label="мои", linewidth=1.5)
        ax.plot(dates, [row[3] for row in rows], label="важные", linewidth=1.5, color="crimson")
        ax.set_title(f"Сообщения по дням (последние {days} дн.)")
        ax.grid(alpha=0.3)
        ax.legend()
        fig.autofmt_xdate()
        return self._save(fig, "activity_by_day.png")

    def plot_top_chats(self, limit: int = 10) -> str:
        """
        Горизонтальные столбцы: самые активные чаты и сколько в них важного
        """
        rows = self.db.get_top_chats(limit)
        if not rows:
            return None
        rows = rows[::-1]
        names = [str(row[0])[:30] for row in rows]
        fig, ax = plt.subplots(figsize=(10, max(3, 0.45 * len(rows) + 1)))
        ax.barh(names, [row[1] for row in rows], label="всего")
        ax.barh(names, [row[2] for row in rows], label="важных", color="crimson")
        ax.set_title("Самые активные чаты")
        ax.legend()
        ax.grid(axis="x", alpha=0.3)
        return self._save(fig, "top_chats.png")

    def plot_by_hour(self) -> str:
        """
        Столбцы: в какие часы приходят сообщения
        """
        rows = dict(self.db.get_activity_by_hour())
        if not rows:
            return None
        hours = list(range(24))
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.bar(hours, [rows.get(h, 0) for h in hours])
        ax.set_xticks(hours)
        ax.set_title("Активность по часам")
        ax.set_xlabel("час")
        ax.grid(axis="y", alpha=0.3)
        return self._save(fig, "by_hour.png")

    def plot_levels(self) -> str:
        """
        Столбцы: распределение сообщений по уровням важности
        """
        rows = self.db.get_level_distribution()
        if not rows:
            return None
        order = ["critical", "high", "medium", "low", "mine"]
        colors = {"critical": "crimson", "high": "orange", "medium": "steelblue", "low": "silver", "mine": "lightgray"}
        data = dict(rows)
        levels = [level for level in order if level in data]
        fig, ax = plt.subplots(figsize=(7, 4))
        ax.bar(levels, [data[level] for level in levels], color=[colors[level] for level in levels])
        ax.set_yscale("log")
        ax.set_title("Уровни важности (лог. шкала)")
        ax.grid(axis="y", alpha=0.3)
        return self._save(fig, "levels.png")

    def plot_top_senders(self, limit: int = 10) -> str:
        """
        Самые активные отправители
        """
        rows = self.db.get_top_senders(limit)
        if not rows:
            return None
        rows = rows[::-1]
        names = [str(row[0])[:25] for row in rows]
        fig, ax = plt.subplots(figsize=(9, max(3, 0.45 * len(rows) + 1)))
        ax.barh(names, [row[1] for row in rows], label="всего")
        ax.barh(names, [row[2] for row in rows], label="важных", color="crimson")
        ax.set_title("Самые активные отправители")
        ax.legend()
        return self._save(fig, "top_senders.png")

    def plot_all(self, days: int = 30) -> list[str]:
        """
        Все графики разом
        Возвращает:
            список путей к созданным файлам
        """
        paths = [
            self.plot_activity_by_day(days),
            self.plot_top_chats(),
            self.plot_by_hour(),
            self.plot_levels(),
            self.plot_top_senders(),
        ]
        return [path for path in paths if path]
