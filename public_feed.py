import time

from aiohttp import web

import bars_v1
import segments
import store


PUBLIC_HEADERS = {'Access-Control-Allow-Origin': '*', 'Cache-Control': 'public, max-age=5'}
CONNECTION = web.AppKey('connection', object)
NODE_ID = web.AppKey('node_id', str)


def public_error(message: str) -> web.Response:
    return web.json_response({'error': message}, status=400, headers=PUBLIC_HEADERS)


async def handle_public_instruments(request: web.Request) -> web.Response:
    instruments = [{'venue': venue, 'symbol': symbol, 'tfs': [tf]} for venue, symbol, tf in segments.COVERAGE]
    return web.json_response({'version': '1', 'instruments': instruments}, headers=PUBLIC_HEADERS)


async def handle_public_bars(request: web.Request) -> web.Response:
    query = request.query
    venue, symbol, tf = query.get('venue', ''), query.get('symbol', ''), query.get('tf', '')
    if venue != 'hyperliquid':
        return public_error('the public feed serves hyperliquid only')
    if (venue, symbol, tf) not in segments.COVERAGE:
        return public_error(f'unknown {venue}/{symbol} {tf}')
    try:
        count, end_ts = int(query.get('bars') or 300), int(query.get('end_ts') or 0)
    except ValueError:
        return public_error('bars and end_ts must be integers')
    if not 1 <= count <= 1000:
        return public_error('bars is 1 to 1000')
    if end_ts > 9223372036854775807 or end_ts < -9223372036854775808:
        return public_error('end_ts is out of range')
    fetched_at = int(time.time() * 1000)
    now_seconds = fetched_at // 1000
    connection = request.app[CONNECTION]
    rows = store.read_bars(connection, symbol, count, end_ts)
    hashes = {}
    for day_start in {segments.day_start_for(row['time']) for row in rows}:
        segment = segments.segment(connection, symbol, day_start, now_seconds)
        if segment:
            hashes[day_start] = segment['segment_hash']
    result = {'version': bars_v1.INTERFACE_VERSION, 'venue': venue, 'symbol': symbol, 'tf': tf,
              'provenance': {'source_venue': venue, 'source_store': 'files', 'live_extended': False,
                             'node_id': request.app[NODE_ID], 'fetched_at': str(fetched_at)},
              'bars': [bars_v1.bar_version_one(row, now_seconds, hashes.get(segments.day_start_for(row['time']), ''))
                       for row in rows]}
    return web.json_response(result, headers=PUBLIC_HEADERS)


async def handle_public_segments(request: web.Request) -> web.Response:
    try:
        day_start = segments.day_start_from_name(request.query.get('day', ''))
    except ValueError:
        return public_error('day is YYYY-MM-DD (UTC)')
    now_seconds = int(time.time())
    if not segments.is_settled(day_start, now_seconds):
        return public_error(f'a day is hashed {segments.SETTLE_SECONDS} s after it ends (UTC); this one is not settled yet')
    day = segments.day_segments_and_root(request.app[CONNECTION], day_start, now_seconds, request.app[NODE_ID])
    return web.json_response({'version': bars_v1.INTERFACE_VERSION, **day}, headers=PUBLIC_HEADERS)


def create_app(connection, node_id: str) -> web.Application:
    app = web.Application()
    app[CONNECTION] = connection
    app[NODE_ID] = node_id
    app.router.add_get('/public/v1/instruments', handle_public_instruments)
    app.router.add_get('/public/v1/bars', handle_public_bars)
    app.router.add_get('/public/v1/segments', handle_public_segments)
    return app
