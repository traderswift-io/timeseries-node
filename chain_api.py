import re

import segments


def valid_hash(value) -> bool:
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def canonical_day(day: str) -> str:
    if segments.day_name(segments.day_start_from_name(day)) != day:
        raise ValueError('day must be YYYY-MM-DD')
    return day


async def fetch_anchors(session, config: dict, day: str) -> list[dict]:
    canonical_day(day)
    route = f'/network/v1/anchors/timeseries/candle_root/{day}'
    async with session.get(config['chain_rest_base_url'] + route) as response:
        response.raise_for_status()
        rows = await response.json()
    if not isinstance(rows, list):
        raise ValueError('Anchors REST response must be a list')
    providers = set()
    for row in rows:
        if not isinstance(row, dict) or (row.get('subnet'), row.get('segment_kind'), row.get('segment_id')) != ('timeseries', 'candle_root', day):
            raise ValueError('unexpected anchor segment')
        provider = row.get('provider_id')
        if not isinstance(provider, str) or not provider or provider in providers:
            raise ValueError('anchor providers must be distinct')
        if not valid_hash(row.get('segment_hash')) or type(row.get('endorsed')) is not bool or type(row.get('vetoed')) is not bool:
            raise ValueError('invalid anchor fields')
        providers.add(provider)
    return rows
