import base64
import json
import secrets
import time
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

import x402_manifest


def url_base64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip('=')


def authorization(config: dict, url: str) -> str:
    key_secret, raw, key = '', b'', None
    try:
        key_secret = x402_manifest.bounded_file(config['facilitator_key_file'], 16384).decode().strip()
        if key_secret.startswith('-----BEGIN'):
            key = serialization.load_pem_private_key(key_secret.encode(), password=None)
            x402_manifest.require(isinstance(key, ec.EllipticCurvePrivateKey) and isinstance(key.curve, ec.SECP256R1))
            algorithm = 'ES256'
        else:
            raw = base64.b64decode(key_secret, validate=True)
            x402_manifest.require(len(raw) in (32, 64))
            key = ed25519.Ed25519PrivateKey.from_private_bytes(raw[:32])
            algorithm = 'EdDSA'
        parsed = urlsplit(url)
        now = int(time.time())
        key_id = config['facilitator_key_id']
        header = {'alg': algorithm, 'typ': 'JWT', 'kid': key_id, 'nonce': secrets.token_hex(16)}
        claims = {'sub': key_id, 'iss': 'cdp', 'nbf': now, 'exp': now + 120,
                  'uris': ['POST ' + parsed.netloc + parsed.path]}
        signing_input = (url_base64(json.dumps(header, separators=(',', ':')).encode()) + '.' +
                         url_base64(json.dumps(claims, separators=(',', ':')).encode())).encode()
        if algorithm == 'ES256':
            first, second = decode_dss_signature(key.sign(signing_input, ec.ECDSA(hashes.SHA256())))
            signature = first.to_bytes(32, 'big') + second.to_bytes(32, 'big')
        else:
            signature = key.sign(signing_input)
        return 'Bearer ' + signing_input.decode() + '.' + url_base64(signature)
    except Exception:
        raise ValueError('facilitator authentication unavailable') from None
    finally:
        del key_secret, raw, key
