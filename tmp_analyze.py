import sqlite3
import os
from datetime import datetime

DB_PATH = "/app/shared/state/trades.db"

def analyze_dispatcher_data():
    if not os.path.exists(DB_PATH):
        print(f"❌ Ошибка: Файл базы данных не найден по пути {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT COUNT(*) FROM dispatcher_features")
        total_records = cursor.fetchone()[0]
        
        if total_records == 0:
            print("⚠️ Таблица dispatcher_features пуста. Бот еще не записал логи.")
            return

        print("=" * 60)
        print(f"📊 АНАЛИЗ ДИСПЕТЧЕРА HYDRA (Всего записей: {total_records})")
        print("=" * 60)

        cursor.execute("SELECT MIN(score), MAX(score), AVG(score) FROM dispatcher_features")
        min_s, max_s, avg_s = cursor.fetchone()
        print(f"📈 Скоринг по рынку: Мин={min_s:.2f} | Макс={max_s:.2f} | Средний={avg_s:.2f}")

        cursor.execute("SELECT mode, COUNT(*) FROM dispatcher_features GROUP BY mode")
        modes = cursor.fetchall()
        print("\n⚙️ Распределение режимов:")
        for mode, count in modes:
            pct = (count / total_records) * 100
            print(f"  - {mode:<15}: {count:<5} записей ({pct:.1f}%)")

        print("\n🔥 Топ-5 монет с максимальным приоритетом (Score):")
        cursor.execute("""
            SELECT symbol, MAX(score), AVG(rvol_spike), MAX(dump_depth), AVG(obi_skew)
            FROM dispatcher_features
            GROUP BY symbol
            ORDER BY MAX(score) DESC
            LIMIT 5
        """)
        top_symbols = cursor.fetchall()
        print(f"  {'Монета':<12} | {'Макс Score':<10} | {'Ср. RVOL':<8} | {'Макс Дамп':<9} | {'Ср. OBI':<8}")
        print("  " + "-" * 56)
        for sym, m_score, a_rvol, m_dump, a_obi in top_symbols:
            print(f"  {sym:<12} | {m_score:<10.2f} | {a_rvol:<8.1f}x | {m_dump:<8.1f}% | {a_obi:<8.2f}")

        print("\n🚨 Топ-3 экстремальных панических дампов за сессию:")
        cursor.execute("""
            SELECT timestamp, symbol, dump_depth, rvol_spike, score, mode
            FROM dispatcher_features
            ORDER BY dump_depth DESC
            LIMIT 3
        """)
        dumps = cursor.fetchall()
        for ts, sym, dump, rvol, score, mode in dumps:
            try:
                dt = datetime.fromtimestamp(ts).strftime('%H:%M:%S')
            except:
                dt = str(ts)
            print(f"  [{dt}] {sym:<10} -> Дамп: {dump:.1f}% | RVOL: {rvol:.1f}x | Score: {score:.2f} ({mode})")

    except sqlite3.OperationalError as e:
        print(f"❌ Ошибка при работе с БД: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    analyze_dispatcher_data()
