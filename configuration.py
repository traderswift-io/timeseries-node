import json
import re
from pathlib import Path
from urllib.parse import urlsplit


def require_strings(config: dict, names: tuple) -> None:
    for name in names:
        if not isinstance(config.get(name), str) or not config[name].strip():
            raise ValueError(f'{name} must be a nonempty string')


def require_url(config: dict, name: str, schemes: tuple) -> None:
    require_strings(config, (name,))
    parsed = urlsplit(config[name])
    if parsed.scheme not in schemes or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(f'{name} must be a base URL without credentials, query or fragment')
    config[name] = config[name].rstrip('/')


def load_config(path: Path, command: str = 'serve') -> dict:
    config = json.loads(path.read_text())
    if not isinstance(config, dict):
        raise ValueError('config must be a JSON object')
    if command in ('anchor', 'verify'):
        require_url(config, 'chain_rest_base_url', ('http', 'https'))
    if command == 'verify':
        require_url(config, 'node_base_url', ('http', 'https'))
        return config
    config['config_path'] = path
    require_strings(config, ('node_id', 'database', 'http_base_url', 'websocket_base_url', 'listen_host'))
    require_url(config, 'http_base_url', ('http', 'https'))
    require_url(config, 'websocket_base_url', ('ws', 'wss'))
    port = config.get('listen_port')
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('listen_port must be an integer from 1 to 65535')
    days = config.setdefault('backfill_days', 30)
    if type(days) is not int or not 1 <= days <= 200:
        raise ValueError('backfill_days must be an integer from 1 to 200')
    database = Path(config['database'])
    config['database'] = database if database.is_absolute() else path.resolve().parent / database
    if command == 'anchor':
        require_strings(config, ('chain_binary', 'chain_keyring_dir', 'chain_key_name', 'chain_id', 'store_provider_id'))
        require_url(config, 'chain_rpc_url', ('http', 'https', 'tcp'))
        if config.setdefault('chain_keyring_backend', 'test') not in ('test', 'file', 'os'):
            raise ValueError('chain_keyring_backend must be test, file or os')
        if not re.fullmatch(r'[A-Za-z0-9._-]{1,64}', config['store_provider_id']):
            raise ValueError('store_provider_id must be a compact provider identifier')
        for name in ('chain_binary', 'chain_keyring_dir'):
            configured_path = Path(config[name])
            config[name] = str(configured_path if configured_path.is_absolute() else path.absolute().parent / configured_path)
    return config
