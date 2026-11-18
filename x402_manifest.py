import base64
import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature


NETWORK = 'eip155:8453'
ASSET = '0x833589fcd6edb6e08f4c7c32d4f71b54bda02913'
SIGNATURE_TYPE = 'tendermint/PubKeySecp256k1'
FIELDS = ('schema chain_id subnet provider_id version maker_name origin api_versions scopes licence '
          'payee network asset amount_atomic terms price_version issued_at expires_at '
          'facilitator_urls refund_contact').split()


def require(condition) -> None:
    if not condition:
        raise ValueError('invalid paid configuration or manifest')


def unique_object(pairs):
    result = {}
    for name, value in pairs:
        require(name not in result)
        result[name] = value
    return result


def parse_json(value):
    return json.loads(value, object_pairs_hook=unique_object)


def canonical(value) -> str:
    if isinstance(value, dict):
        names = sorted(value, key=lambda name: name.encode('utf-16-be'))
        return '{' + ','.join(canonical(name) + ':' + canonical(value[name]) for name in names) + '}'
    if isinstance(value, list):
        return '[' + ','.join(canonical(item) for item in value) + ']'
    require(value is None or isinstance(value, (str, bool)))
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def bounded_text(value, maximum=256) -> None:
    require(isinstance(value, str) and 0 < len(value.encode('utf-16-le')) // 2 <= maximum)
    require(re.search(r'[\x00-\x1f\x7f]', value) is None)


def unsigned_integer(value, positive=False) -> int:
    require(isinstance(value, str) and re.fullmatch(r'(0|[1-9][0-9]{0,19})', value))
    integer = int(value)
    require(integer <= 18446744073709551615 and (not positive or integer > 0))
    return integer


def https_url(value, origin_only=False) -> None:
    bounded_text(value, 2048)
    parsed = urlsplit(value)
    require(parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password)
    require(not parsed.fragment and '#' not in value and '\\' not in value and '%' not in value)
    require(parsed.hostname == parsed.hostname.lower() and parsed.port != 443)
    require(not any(ord(character) <= 32 or ord(character) > 126 for character in value))
    authority = parsed.hostname + (f':{parsed.port}' if parsed.port else '')
    expected = 'https://' + authority
    if not origin_only:
        expected += parsed.path or '/'
        if parsed.query:
            expected += '?' + parsed.query
    require(value == expected and not any(part in ('.', '..') for part in parsed.path.split('/')))


def validate_manifest(manifest: dict) -> bytes:
    require(isinstance(manifest, dict) and set(manifest) == set(FIELDS))
    require(manifest['schema'] == 'traderswift.frontend-manifest.v1')
    require(manifest['chain_id'] == 'traderswift-1' and manifest['subnet'] == 'timeseries')
    require(isinstance(manifest['provider_id'], str) and re.fullmatch(r'[A-Za-z0-9._-]{1,64}', manifest['provider_id']))
    for name in ('maker_name', 'licence', 'refund_contact'):
        bounded_text(manifest[name])
    for name in ('api_versions', 'scopes', 'facilitator_urls'):
        values = manifest[name]
        require(isinstance(values, list) and 0 < len(values) <= 32)
        for value in values:
            bounded_text(value)
        require(len(set(values)) == len(values))
    https_url(manifest['origin'], True)
    for value in manifest['facilitator_urls']:
        if urlsplit(value).hostname == '127.0.0.1':
            parsed = urlsplit(value)
            require(parsed.scheme == 'http' and 47000 <= parsed.port <= 47999)
            require(not parsed.username and not parsed.password and not parsed.query and not parsed.fragment)
        else:
            https_url(value)
    require(isinstance(manifest['payee'], str) and re.fullmatch(r'0x[0-9a-f]{40}', manifest['payee']))
    require(manifest['payee'] != '0x' + '0' * 40)
    require(manifest['network'] == NETWORK and manifest['asset'] == ASSET)
    for name in ('version', 'price_version'):
        unsigned_integer(manifest[name], True)
    issued = unsigned_integer(manifest['issued_at'])
    expires = unsigned_integer(manifest['expires_at'], True)
    require(0 < expires - issued <= 604800)
    unsigned_integer(manifest['amount_atomic'], True)
    terms = manifest['terms']
    require(isinstance(terms, dict) and set(terms) == {'kind', 'entitlement', 'duration_seconds'})
    bounded_text(terms['entitlement'])
    require(terms['kind'] == 'per-use' and terms['duration_seconds'] == '0')
    return canonical(manifest).encode('utf-8')


def bounded_file(path: Path, maximum: int) -> bytes:
    with path.open('rb') as source:
        value = source.read(maximum + 1)
    require(len(value) <= maximum)
    return value


def cosmos_address(public_key: bytes) -> str:
    digest = hashlib.new('ripemd160', hashlib.sha256(public_key).digest()).digest()
    accumulator, bits, values = 0, 0, []
    for byte in digest:
        accumulator = (accumulator << 8) | byte
        bits += 8
        while bits >= 5:
            bits -= 5
            values.append((accumulator >> bits) & 31)
    if bits:
        values.append((accumulator << (5 - bits)) & 31)
    prefix = 'cosmos'
    expanded = [ord(char) >> 5 for char in prefix] + [0] + [ord(char) & 31 for char in prefix]
    checksum = 1
    for value in expanded + values + [0] * 6:
        high = checksum >> 25
        checksum = ((checksum & 0x1ffffff) << 5) ^ value
        for index, generator in enumerate((0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3)):
            if (high >> index) & 1:
                checksum ^= generator
    checksum ^= 1
    values += [(checksum >> (5 * (5 - index))) & 31 for index in range(6)]
    alphabet = 'qpzry9x8gf2tvdw0s3jn54khce6mua7l'
    return prefix + '1' + ''.join(alphabet[value] for value in values)


def signing_document(address: str, manifest_bytes: bytes) -> bytes:
    signed_text = b'traderswift frontend manifest v1\n' + manifest_bytes
    document = {'account_number': '0', 'chain_id': '', 'fee': {'amount': [], 'gas': '0'},
                'memo': '', 'msgs': [{'type': 'sign/MsgSignData', 'value': {
                    'data': base64.b64encode(signed_text).decode(), 'signer': address}}], 'sequence': '0'}
    return canonical(document).encode()


def load_manifest(config: dict) -> dict:
    raw = bounded_file(config['manifest_file'], 65536)
    manifest = parse_json(raw)
    require(validate_manifest(manifest) == raw)
    require(int(manifest['issued_at']) <= int(time.time()) < int(manifest['expires_at']))
    for manifest_name, config_name in (('payee', 'payTo'), ('amount_atomic', 'amount_atomic'), ('origin', 'origin')):
        require(manifest[manifest_name] == config[config_name])
    require(manifest['provider_id'] == config['serve_provider_id'])
    require(manifest['scopes'] == [config['scope']] and manifest['api_versions'] == ['2'])
    require(manifest['terms']['entitlement'] == config['scope'])
    require(manifest['facilitator_urls'] == [config['facilitator_url']])
    envelope = parse_json(bounded_file(config['envelope_file'], 1024))
    require(isinstance(envelope, dict) and set(envelope) == {'kid', 'address', 'pub_key', 'signature'})
    pin = config['manifest_pin']
    require(isinstance(pin, dict) and set(pin) == {'kid', 'type', 'address', 'publicKey'})
    bounded_text(pin['kid'], 128)
    require(pin['type'] == SIGNATURE_TYPE)
    require(envelope['kid'] == pin['kid'] and envelope['address'] == pin['address'])
    require(envelope['pub_key'] == {'type': pin['type'], 'value': pin['publicKey']})
    public_key = base64.b64decode(pin['publicKey'], validate=True)
    require(len(public_key) == 33 and public_key[0] in (2, 3))
    require(cosmos_address(public_key) == pin['address'])
    signature = base64.b64decode(envelope['signature'], validate=True)
    require(len(signature) == 64)
    first, second = int.from_bytes(signature[:32], 'big'), int.from_bytes(signature[32:], 'big')
    order = 0xfffffffffffffffffffffffffffffffebaaedce6af48a03bbfd25e8cd0364141
    require(0 < first < order and 0 < second <= order // 2)
    verifier = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256K1(), public_key)
    verifier.verify(encode_dss_signature(first, second), signing_document(pin['address'], raw), ec.ECDSA(hashes.SHA256()))
    signature_header = base64.urlsafe_b64encode(canonical(envelope).encode()).decode().rstrip('=')
    require(len(signature_header) <= 1024)
    return {'manifest': manifest, 'manifest_bytes': raw, 'signature_header': signature_header}
