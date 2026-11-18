import json
from pathlib import Path
from urllib.parse import urlsplit


def load_config(path: Path) -> dict:
    config = json.loads(path.read_text())
    for name in ('node_id', 'database', 'http_base_url', 'websocket_base_url', 'listen_host'):
        if not isinstance(config.get(name), str) or not config[name].strip():
            raise ValueError(f'{name} must be a nonempty string')
    for name, schemes in (('http_base_url', ('http', 'https')), ('websocket_base_url', ('ws', 'wss'))):
        parsed = urlsplit(config[name])
        if parsed.scheme not in schemes or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError(f'{name} must be a base URL without credentials, query or fragment')
        config[name] = config[name].rstrip('/')
    port = config.get('listen_port')
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('listen_port must be an integer from 1 to 65535')
    days = config.setdefault('backfill_days', 30)
    if type(days) is not int or not 1 <= days <= 200:
        raise ValueError('backfill_days must be an integer from 1 to 200')
    database = Path(config['database'])
    config['database'] = database if database.is_absolute() else path.resolve().parent / database
    return config
