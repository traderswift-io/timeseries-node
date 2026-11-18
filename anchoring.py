import asyncio
import json

from aiohttp import ClientError

import chain_api
import segments


def prepare_store(connection) -> None:
    connection.execute(
        'CREATE TABLE IF NOT EXISTS anchor_submissions ('
        'chain_id TEXT NOT NULL, provider_id TEXT NOT NULL, day TEXT NOT NULL, '
        'candle_root TEXT NOT NULL, status TEXT NOT NULL, '
        'PRIMARY KEY (chain_id, provider_id, day))'
    )
    connection.commit()


def transaction_arguments(config: dict, day: str, candle_root: str) -> list[str]:
    return [config['chain_binary'], 'tx', 'network', 'anchor',
            '--subnet', 'timeseries', '--segment-kind', 'candle_root',
            '--segment-id', day, '--segment-hash', candle_root,
            '--provider-id', config['store_provider_id'], '--from', config['chain_key_name'],
            '--keyring-dir', config['chain_keyring_dir'], '--keyring-backend', config['chain_keyring_backend'],
            '--chain-id', config['chain_id'],
            '--node', config['chain_rpc_url'], '--output', 'json', '--yes', '--broadcast-mode', 'sync']


async def submit_anchor(config: dict, day: str, candle_root: str) -> str:
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            *transaction_arguments(config, day, candle_root),
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL
        )
        output, _ = await asyncio.wait_for(process.communicate(), timeout=60)
        if process.returncode != 0:
            return 'uncertain'
        response = json.loads(output)
        if not isinstance(response, dict):
            return 'uncertain'
        response = response.get('tx_response', response)
        if not isinstance(response, dict) or str(response.get('code')) != '0':
            return 'uncertain'
        transaction_hash = response.get('txhash')
        if not isinstance(transaction_hash, str) or len(transaction_hash) != 64 or any(character not in '0123456789abcdefABCDEF' for character in transaction_hash):
            return 'uncertain'
        return 'submitted'
    except (OSError, ValueError, asyncio.TimeoutError):
        return 'uncertain'
    finally:
        if process is not None and process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()


def record_status(connection, identity: tuple, candle_root: str, status: str) -> None:
    with connection:
        connection.execute(
            'INSERT INTO anchor_submissions VALUES (?, ?, ?, ?, ?) '
            'ON CONFLICT (chain_id, provider_id, day) DO UPDATE SET status=excluded.status',
            (*identity, candle_root, status)
        )


async def anchor_day(session, config: dict, connection, day_start: int, now_seconds: int) -> None:
    day = segments.day_name(day_start)
    identity = (config['chain_id'], config['store_provider_id'], day)
    previous = connection.execute(
        'SELECT candle_root, status FROM anchor_submissions WHERE chain_id=? AND provider_id=? AND day=?', identity
    ).fetchone()
    if previous is not None and previous['status'] in ('confirmed', 'conflict'):
        return
    payload = segments.day_segments_and_root(connection, day_start, now_seconds, config['node_id'])
    if payload is None or not payload['candle_root']:
        return
    candle_root = previous['candle_root'] if previous is not None else payload['candle_root']
    anchors = await chain_api.fetch_anchors(session, config, day)
    own_anchor = next((row for row in anchors if row['provider_id'] == config['store_provider_id']), None)
    if own_anchor is not None:
        status = 'confirmed' if own_anchor['segment_hash'] == candle_root and not own_anchor['vetoed'] else 'conflict'
        record_status(connection, identity, candle_root, status)
        print(f'anchor {day} {status}', flush=True)
        return
    if previous is not None:
        return
    record_status(connection, identity, candle_root, 'uncertain')
    status = await submit_anchor(config, day, candle_root)
    record_status(connection, identity, candle_root, status)
    print(f'anchor {day} {status}', flush=True)


async def on_bar_event(session, config: dict, connection, now_seconds: int) -> None:
    last_settled_day = segments.day_start_for(now_seconds - segments.DAY_SECONDS - segments.SETTLE_SECONDS)
    days = connection.execute(
        'SELECT DISTINCT time - time % 86400 AS day_start FROM candles '
        'WHERE venue=? AND tf=? AND time < ? ORDER BY day_start',
        ('hyperliquid', '1h', last_settled_day + segments.DAY_SECONDS)
    ).fetchall()
    for row in days:
        try:
            await anchor_day(session, config, connection, row['day_start'], now_seconds)
        except (ClientError, ValueError, asyncio.TimeoutError):
            print(f'anchor {segments.day_name(row["day_start"])} unavailable', flush=True)
