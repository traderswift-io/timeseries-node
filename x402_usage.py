import fcntl
import json
import re
from datetime import datetime, timezone
from uuid import uuid4


def append_record(handle, record: dict) -> None:
    handle.write(json.dumps(record, separators=(',', ':'), allow_nan=False) + '\n')
    handle.flush()


def open_append_file(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open('a+', encoding='utf-8')
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except BaseException:
        handle.close()
        raise


def open_usage(config: dict) -> dict:
    ledger = open_append_file(config['ledger_file'])
    meter = None
    try:
        payments, counts = {}, {}
        ledger.seek(0)
        for line in ledger:
            record = json.loads(line)
            if set(record) != {'payment_id', 'payer', 'day', 'status'}:
                raise ValueError('invalid payment ledger')
            payment_id, payer, day, status = (record[name] for name in ('payment_id', 'payer', 'day', 'status'))
            if not re.fullmatch('[0-9a-f]{64}', payment_id) or not re.fullmatch('0x[0-9a-f]{40}', payer):
                raise ValueError('invalid payment ledger')
            if datetime.strptime(day, '%Y-%m-%d').strftime('%Y-%m-%d') != day:
                raise ValueError('invalid payment ledger')
            previous = payments.get(payment_id)
            identity = (payer, day)
            if status == 'pending' and previous is None:
                counts[identity] = counts.get(identity, 0) + 1
            elif status in ('settled', 'failed') and previous == {**record, 'status': 'pending'}:
                if status == 'failed':
                    counts[identity] -= 1
            else:
                raise ValueError('invalid payment ledger transition')
            payments[payment_id] = record
        ledger.seek(0, 2)
        meter = open_append_file(config['meter_file'])
        return {'ledger': ledger, 'meter': meter, 'payments': payments, 'counts': counts}
    except BaseException:
        ledger.close()
        if meter is not None:
            meter.close()
        raise


def request_day(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime('%Y-%m-%d')


def reserve(usage: dict, payment_id: str, payer: str, day: str) -> None:
    record = {'payment_id': payment_id, 'payer': payer, 'day': day, 'status': 'pending'}
    append_record(usage['ledger'], record)
    usage['payments'][payment_id] = record
    identity = (payer, day)
    usage['counts'][identity] = usage['counts'].get(identity, 0) + 1


def finish(usage: dict, payment_id: str, status: str) -> None:
    record = {**usage['payments'][payment_id], 'status': status}
    append_record(usage['ledger'], record)
    usage['payments'][payment_id] = record
    if status == 'failed':
        identity = (record['payer'], record['day'])
        usage['counts'][identity] -= 1


def meter_request(usage: dict, config: dict, request, response, timestamp: int) -> None:
    resource = request.path if request.path in (
        '/public/v1/instruments', '/public/v1/bars', '/public/v1/segments', '/segments',
        '/paid/v1/bars', '/.well-known/traderswift-manifest.json') else 'other'
    delivered = request.method == 'GET' and response.status == 200 and resource in (
        '/public/v1/bars', '/public/v1/segments', '/segments', '/paid/v1/bars')
    record = {'schema': 'rec.v1', 'event_id': str(uuid4()), 'ts': str(timestamp),
              'kind': 'bytes' if delivered else 'view', 'node_id': config['node_id'],
              'resource': resource, 'status': str(response.status),
              'bytes': str(len(response.body or b'') if request.method != 'HEAD' else 0)}
    if delivered:
        record.update({'store_provider_id': config['store_provider_id'], 'serve_provider_id': config['serve_provider_id']})
    append_record(usage['meter'], record)
