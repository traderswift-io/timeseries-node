from decimal import Decimal


INTERFACE_VERSION = '1'
PRICE_FIELDS = ('open', 'high', 'low', 'close', 'volume')


def decimal_string(value) -> str:
    text = format(Decimal(repr(float(value))), 'f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return text if text not in ('', '-0') else '0'


def bar_version_one(bar: dict, now_seconds: int, segment_hash: str = '') -> dict:
    converted = {'time': int(bar['time'])}
    for field in PRICE_FIELDS:
        converted[field] = decimal_string(bar[field])
    converted['status'] = 'live' if bar['time'] + 3600 > now_seconds else 'closed'
    if segment_hash:
        converted['segment_hash'] = segment_hash
    return converted
