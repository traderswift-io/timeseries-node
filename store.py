import sqlite3
from decimal import Decimal
from pathlib import Path

from bars_v1 import PRICE_FIELDS


def open_store(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA journal_mode=WAL')
    connection.execute(
        'CREATE TABLE IF NOT EXISTS candles ('
        'venue TEXT NOT NULL, symbol TEXT NOT NULL, tf TEXT NOT NULL, time INTEGER NOT NULL, '
        'open TEXT NOT NULL, high TEXT NOT NULL, low TEXT NOT NULL, close TEXT NOT NULL, '
        'volume TEXT NOT NULL, PRIMARY KEY (venue, symbol, tf, time))'
    )
    connection.commit()
    return connection


def write_bars(connection, symbol: str, rows: list[dict]) -> None:
    values = [('hyperliquid', symbol, '1h', row['time'], *(str(row[field]) for field in PRICE_FIELDS))
              for row in rows]
    with connection:
        connection.executemany(
            'INSERT INTO candles VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) '
            'ON CONFLICT (venue, symbol, tf, time) DO UPDATE SET '
            'open=excluded.open, high=excluded.high, low=excluded.low, '
            'close=excluded.close, volume=excluded.volume', values
        )


def converted_rows(rows) -> list[dict]:
    return [{'time': row['time'], **{field: Decimal(row[field]) for field in PRICE_FIELDS}} for row in rows]


def read_bars(connection, symbol: str, count: int, end_ts: int) -> list[dict]:
    rows = connection.execute(
        'SELECT * FROM candles WHERE venue=? AND symbol=? AND tf=? AND (? <= 0 OR time <= ?) '
        'ORDER BY time DESC LIMIT ?', ('hyperliquid', symbol, '1h', end_ts, end_ts, count)
    ).fetchall()
    return converted_rows(reversed(rows))


def read_day(connection, symbol: str, day_start: int) -> list[dict]:
    rows = connection.execute(
        'SELECT * FROM candles WHERE venue=? AND symbol=? AND tf=? AND time >= ? AND time < ? '
        'ORDER BY time', ('hyperliquid', symbol, '1h', day_start, day_start + 86400)
    ).fetchall()
    return converted_rows(rows)


def latest_time(connection, symbol: str) -> int | None:
    return connection.execute(
        'SELECT MAX(time) FROM candles WHERE venue=? AND symbol=? AND tf=?',
        ('hyperliquid', symbol, '1h')
    ).fetchone()[0]
