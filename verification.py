import hashlib
import json

from aiohttp import ClientSession, ClientTimeout

import chain_api
import segments


def recompute_root(payload: dict, day: str) -> str:
    if not isinstance(payload, dict) or payload.get('day') != day or payload.get('charter_version') != segments.CHARTER_VERSION:
        raise ValueError('unexpected segment day or charter')
    rows = payload.get('segments')
    if not isinstance(rows, list):
        raise ValueError('segments must be a list')
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('invalid segment')
        series = (row.get('venue'), row.get('symbol'), row.get('tf'))
        if series not in segments.COVERAGE or series in seen or not chain_api.valid_hash(row.get('segment_hash')):
            raise ValueError('invalid or duplicate covered segment')
        seen.add(series)
    return hashlib.sha256(segments.root_bytes(rows)).hexdigest() if rows else ''


def agreement(candle_root: str, anchors: list[dict]) -> str:
    eligible = [row for row in anchors if row['endorsed'] and not row['vetoed']]
    if sum(row['segment_hash'] == candle_root for row in eligible) >= 2:
        return 'agree'
    return 'short' if len(eligible) < 2 else 'disagree'


async def verify_day(config: dict, day: str) -> dict:
    chain_api.canonical_day(day)
    async with ClientSession(timeout=ClientTimeout(total=60)) as session:
        anchors = await chain_api.fetch_anchors(session, config, day)
        async with session.get(config['node_base_url'] + '/segments', params={'day': day}) as response:
            response.raise_for_status()
            root = recompute_root(await response.json(), day)
    return {'day': day, 'candle_root': root, 'result': agreement(root, anchors)}


async def print_verification(config: dict, day: str) -> None:
    print(json.dumps(await verify_day(config, day), sort_keys=True))
