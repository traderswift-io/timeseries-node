import re
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

import x402_manifest


def load_paid(config: dict, config_path: Path) -> dict | None:
    if 'paid' not in config:
        return None
    try:
        paid = dict(config['paid'])
        for name in ('price_usdc', 'payTo', 'facilitator_url', 'credential_file', 'origin', 'scope',
                     'meter_file', 'ledger_file', 'manifest_file', 'envelope_file', 'serve_provider_id'):
            x402_manifest.bounded_text(paid[name], 2048)
        x402_manifest.require(re.fullmatch(r'(0|[1-9][0-9]{0,13})(\.[0-9]{1,6})?', paid['price_usdc']))
        price = Decimal(paid['price_usdc'])
        x402_manifest.require(price.is_finite() and price > 0 and price <= Decimal('18446744073709.551615'))
        atomic = price * Decimal('1000000')
        x402_manifest.require(atomic == atomic.to_integral_value())
        paid['amount_atomic'] = str(int(atomic))
        x402_manifest.require(type(paid['daily_cap']) is int and paid['daily_cap'] > 0)
        x402_manifest.require(type(paid['max_timeout_seconds']) is int and 1 <= paid['max_timeout_seconds'] <= 300)
        x402_manifest.https_url(paid['origin'], True)
        x402_manifest.bounded_text(config['store_provider_id'], 64)
        paid['store_provider_id'] = config['store_provider_id']
        paid['node_id'] = config['node_id']
        parsed = urlsplit(paid['facilitator_url'])
        x402_manifest.require(not paid['facilitator_url'].endswith('/'))
        x402_manifest.require(parsed.scheme == 'https' or (parsed.scheme == 'http' and parsed.hostname == '127.0.0.1'
                                                         and 47000 <= parsed.port <= 47999))
        x402_manifest.require(parsed.hostname and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment)
        for name in ('credential_file', 'meter_file', 'ledger_file', 'manifest_file', 'envelope_file'):
            candidate = Path(paid[name])
            paid[name] = (config_path.resolve().parent / candidate).resolve()
        root = Path(__file__).resolve().parent
        x402_manifest.require(not paid['credential_file'].is_relative_to(root))
        paths = [paid[name] for name in ('credential_file', 'meter_file', 'ledger_file', 'manifest_file', 'envelope_file')]
        paths += [config_path.resolve(), Path(config['database']).resolve()]
        x402_manifest.require(len(paths) == len(set(paths)))
        paid.update(x402_manifest.load_manifest(paid))
        return paid
    except Exception:
        raise ValueError('paid route startup refused: invalid or missing configuration') from None
