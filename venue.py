import json
import time
from decimal import Decimal, InvalidOperation

from aiohttp import WSMsgType

import bars_v1
import segments
import store


VENUE_FIELDS = ('o', 'h', 'l', 'c', 'v')


def decode_json(text: str):
    return json.loads(text, parse_float=Decimal)


def candle_bar(candle: dict, symbol: str) -> dict:
    if candle.get('s') != symbol or candle.get('i') != '1h':
        raise ValueError('unexpected candle series')
    timestamp = candle.get('t')
    if type(timestamp) is not int or timestamp < 0 or timestamp % 3600000:
        raise ValueError('candle timestamp must be an hourly open in milliseconds')
    bar = {'time': timestamp // 1000}
    for field, venue_field in zip(bars_v1.PRICE_FIELDS, VENUE_FIELDS):
        try:
            value = Decimal(str(candle[venue_field]))
            if not value.is_finite() or value < 0 or not Decimal(bars_v1.decimal_string(value)).is_finite():
                raise ValueError('candle values must be finite and nonnegative')
        except (InvalidOperation, KeyError) as error:
            raise ValueError('invalid candle value') from error
        bar[field] = value
    return bar


async def open_stream(session, config: dict):
    websocket = await session.ws_connect(config['websocket_base_url'] + '/ws')
    try:
        for _, symbol, _ in segments.COVERAGE:
            await websocket.send_json({'method': 'subscribe', 'subscription': {
                'type': 'candle', 'coin': symbol, 'interval': '1h'}})
    except BaseException:
        await websocket.close()
        raise
    return websocket


async def backfill(session, config: dict, connection) -> None:
    now_seconds = int(time.time())
    first_start = segments.day_start_for(now_seconds) - config['backfill_days'] * segments.DAY_SECONDS
    for _, symbol, _ in segments.COVERAGE:
        latest = store.latest_time(connection, symbol)
        start = first_start if latest is None else max(first_start, latest - 2 * segments.DAY_SECONDS)
        while start <= now_seconds:
            end = min(start + 1000 * 3600 - 1, now_seconds)
            payload = {'type': 'candleSnapshot', 'req': {
                'coin': symbol, 'interval': '1h', 'startTime': start * 1000, 'endTime': end * 1000}}
            async with session.post(config['http_base_url'] + '/info', json=payload) as response:
                response.raise_for_status()
                candles = await response.json(loads=decode_json)
            if not isinstance(candles, list):
                raise ValueError('candleSnapshot must return a list')
            rows = [candle_bar(candle, symbol) for candle in candles]
            if any(not start <= row['time'] <= end for row in rows):
                raise ValueError('candleSnapshot returned an out-of-range candle')
            store.write_bars(connection, symbol, rows)
            start = end + 1


async def consume_stream(websocket, connection, on_bar_event=None) -> None:
    async for message in websocket:
        if message.type == WSMsgType.ERROR:
            raise ConnectionError('venue websocket failed') from websocket.exception()
        if message.type != WSMsgType.TEXT:
            continue
        payload = decode_json(message.data)
        if payload.get('channel') == 'error':
            raise ValueError('venue rejected websocket subscription')
        if payload.get('channel') != 'candle':
            continue
        candles = payload['data']
        if isinstance(candles, dict):
            candles = [candles]
        if not isinstance(candles, list):
            raise ValueError('candle stream data must be an object or list')
        for candle in candles:
            symbol = candle.get('s')
            if ('hyperliquid', symbol, candle.get('i')) not in segments.COVERAGE:
                raise ValueError('unexpected websocket candle series')
            store.write_bars(connection, symbol, [candle_bar(candle, symbol)])
        if candles and on_bar_event is not None:
            await on_bar_event(int(time.time()))
    if websocket.exception():
        raise ConnectionError('venue websocket failed') from websocket.exception()


async def extend(session, config: dict, connection, websocket, on_bar_event=None) -> None:
    try:
        while True:
            await consume_stream(websocket, connection, on_bar_event)
            await websocket.close()
            websocket = await open_stream(session, config)
            await backfill(session, config, connection)
    finally:
        await websocket.close()
