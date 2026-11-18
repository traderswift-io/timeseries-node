import hashlib
from datetime import datetime, timezone

import bars_v1
import store


CHARTER_VERSION = '1'
SETTLE_SECONDS = 7200
DAY_SECONDS = 86400
COVERAGE = tuple(('hyperliquid', symbol, '1h') for symbol in ('BNB', 'BTC', 'DOGE', 'ETH', 'HYPE', 'SOL', 'XRP'))


def day_start_for(unix_seconds: int) -> int:
    return unix_seconds - unix_seconds % DAY_SECONDS


def day_name(day_start: int) -> str:
    return datetime.fromtimestamp(day_start, timezone.utc).strftime('%Y-%m-%d')


def day_start_from_name(name: str) -> int:
    parsed = datetime.strptime(name, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def is_settled(day_start: int, now_seconds: int) -> bool:
    return now_seconds >= day_start + DAY_SECONDS + SETTLE_SECONDS


def segment_bytes(venue: str, symbol: str, tf: str, day_start: int, stored_bars: list[dict]) -> bytes:
    lines = [f'{venue},{symbol},{tf},{day_name(day_start)}\n']
    for bar in stored_bars:
        values = ','.join(bars_v1.decimal_string(bar[field]) for field in bars_v1.PRICE_FIELDS)
        lines.append(f'{int(bar["time"])},{values}\n')
    return ''.join(lines).encode()


def root_bytes(segment_rows: list[dict]) -> bytes:
    ordered = sorted(segment_rows, key=lambda row: (row['venue'], row['symbol'], row['tf']))
    return ''.join(f'{row["venue"]},{row["symbol"]},{row["tf"]},{row["segment_hash"]}\n' for row in ordered).encode()


def segment(connection, symbol: str, day_start: int, now_seconds: int) -> dict | None:
    if ('hyperliquid', symbol, '1h') not in COVERAGE or not is_settled(day_start, now_seconds):
        return None
    stored_bars = store.read_day(connection, symbol, day_start)
    if not stored_bars:
        return None
    segment_hash = hashlib.sha256(segment_bytes('hyperliquid', symbol, '1h', day_start, stored_bars)).hexdigest()
    return {'venue': 'hyperliquid', 'symbol': symbol, 'tf': '1h',
            'segment_hash': segment_hash, 'bars': str(len(stored_bars))}


def day_segments_and_root(connection, day_start: int, now_seconds: int, node_id: str) -> dict | None:
    if not is_settled(day_start, now_seconds):
        return None
    segment_rows = [row for _, symbol, _ in COVERAGE
                    if (row := segment(connection, symbol, day_start, now_seconds))]
    candle_root = hashlib.sha256(root_bytes(segment_rows)).hexdigest() if segment_rows else ''
    return {'day': day_name(day_start), 'charter_version': CHARTER_VERSION, 'node_id': node_id,
            'candle_root': candle_root, 'segments': segment_rows}
