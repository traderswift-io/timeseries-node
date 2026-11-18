import asyncio
import base64
import hashlib
import json
import re
import time

from aiohttp import ClientSession, ClientTimeout, DummyCookieJar, web

import public_feed
import x402_manifest
import x402_usage


STATE = web.AppKey('paid_state', dict)
HEADERS = {'Cache-Control': 'no-store', 'Access-Control-Allow-Origin': '*',
           'Access-Control-Expose-Headers': 'PAYMENT-REQUIRED, PAYMENT-RESPONSE, X-PAYMENT-RESPONSE, X-TraderSwift-Manifest-Signature',
           'X-Content-Type-Options': 'nosniff'}


def encoded_json(value) -> str:
    return base64.b64encode(json.dumps(value, separators=(',', ':')).encode()).decode()


def requirements(config: dict) -> dict:
    return {'scheme': 'exact', 'network': x402_manifest.NETWORK, 'amount': config['amount_atomic'],
            'asset': x402_manifest.ASSET, 'payTo': config['payTo'],
            'maxTimeoutSeconds': config['max_timeout_seconds'],
            'extra': {'name': 'USD Coin', 'version': '2', 'assetTransferMethod': 'eip3009'}}


def payment_required(state: dict, error='payment_required') -> web.Response:
    body = {'x402Version': 2, 'resource': state['resource'], 'accepts': [state['requirements']], 'error': error}
    return web.json_response(body, status=402, headers={**HEADERS, 'PAYMENT-REQUIRED': encoded_json(body)})


def unavailable(error='facilitator_unavailable') -> web.Response:
    return web.json_response({'error': error}, status=503, headers=HEADERS)


def fresh(config: dict) -> bool:
    return int(config['manifest']['issued_at']) <= int(time.time()) < int(config['manifest']['expires_at'])


def validate_payment(state: dict, header: str):
    x402_manifest.require(len(header) <= 16384)
    payment = x402_manifest.parse_json(base64.b64decode(header, validate=True))
    x402_manifest.require(type(payment['x402Version']) is int and payment['x402Version'] == 2)
    x402_manifest.require(payment['accepted'] == state['requirements'])
    x402_manifest.require('resource' not in payment or payment['resource'] == state['resource'])
    payload = payment['payload']
    authorization = payload['authorization']
    x402_manifest.require(set(payload) == {'signature', 'authorization'})
    x402_manifest.require(set(authorization) == {'from', 'to', 'value', 'validAfter', 'validBefore', 'nonce'})
    x402_manifest.require(re.fullmatch(r'0x[0-9a-fA-F]{130}', payload['signature']))
    x402_manifest.require(re.fullmatch(r'0x[0-9a-fA-F]{40}', authorization['from']))
    x402_manifest.require(authorization['from'].lower() != '0x' + '0' * 40)
    x402_manifest.require(authorization['to'] == state['requirements']['payTo'])
    x402_manifest.require(authorization['value'] == state['requirements']['amount'])
    x402_manifest.require(re.fullmatch(r'0x[0-9a-fA-F]{64}', authorization['nonce']))
    start = x402_manifest.unsigned_integer(authorization['validAfter'])
    expiry = x402_manifest.unsigned_integer(authorization['validBefore'])
    now = int(time.time())
    x402_manifest.require(start < now < expiry <= now + state['requirements']['maxTimeoutSeconds'])
    payer = authorization['from'].lower()
    identity = '|'.join((x402_manifest.NETWORK, x402_manifest.ASSET, payer, authorization['nonce'].lower()))
    return payment, hashlib.sha256(identity.encode()).hexdigest(), payer


async def facilitator_call(config: dict, operation: str, payment: dict, offer: dict) -> dict:
    authorization = x402_manifest.bounded_file(config['credential_file'], 16384).decode().strip()
    try:
        x402_manifest.require(authorization and '\n' not in authorization and '\r' not in authorization)
        body = {'x402Version': 2, 'paymentPayload': payment, 'paymentRequirements': offer}
        async with ClientSession(timeout=ClientTimeout(total=10), trust_env=False, cookie_jar=DummyCookieJar()) as session:
            async with session.post(config['facilitator_url'] + '/' + operation, json=body,
                                    headers={'Authorization': authorization}, allow_redirects=False) as response:
                x402_manifest.require(response.status == 200)
                raw = bytearray()
                async for chunk in response.content.iter_chunked(4096):
                    raw.extend(chunk)
                    x402_manifest.require(len(raw) <= 65536)
                result = x402_manifest.parse_json(raw)
                x402_manifest.require(isinstance(result, dict))
                return result
    finally:
        del authorization


def payer_matches(result: dict, payer: str) -> bool:
    return isinstance(result.get('payer'), str) and result['payer'].lower() == payer


def settlement_receipt(result: dict, state: dict, payer: str) -> dict:
    x402_manifest.require(result.get('success') is True and result.get('network') == x402_manifest.NETWORK)
    x402_manifest.require(payer_matches(result, payer))
    x402_manifest.require('amount' not in result or result['amount'] == state['requirements']['amount'])
    x402_manifest.require(re.fullmatch(r'0x[0-9a-fA-F]{64}', result.get('transaction', '')))
    return {'success': True, 'network': x402_manifest.NETWORK, 'transaction': result['transaction'],
            'amount': state['requirements']['amount']}


async def settle_bars(request, state: dict, header: str, day: str) -> web.Response:
    config, usage = state['config'], state['usage']
    try:
        payment, payment_id, payer = validate_payment(state, header)
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
        return payment_required(state, 'invalid_payment')
    if payment_id in usage['payments']:
        return payment_required(state, 'payment_replayed')
    if usage['counts'].get((payer, day), 0) >= config['daily_cap']:
        return web.json_response({'error': 'daily_cap_exceeded'}, status=429, headers=HEADERS)
    try:
        verification = await facilitator_call(config, 'verify', payment, state['requirements'])
    except Exception:
        return unavailable()
    if verification.get('isValid') is not True or not payer_matches(verification, payer):
        return payment_required(state, 'invalid_payment')
    try:
        validate_payment(state, header)
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
        return payment_required(state, 'invalid_payment')
    if not fresh(config):
        return unavailable('manifest_expired')
    response = await public_feed.handle_public_bars(request)
    if response.status != 200:
        response.headers.update(HEADERS)
        return response
    x402_usage.reserve(usage, payment_id, payer, day)
    try:
        result = await facilitator_call(config, 'settle', payment, state['requirements'])
        if result.get('success') is False and result.get('errorReason') != 'settlement_pending':
            try:
                x402_usage.finish(usage, payment_id, 'failed')
            except Exception:
                state['failed'] = True
                raise
            return payment_required(state, 'settlement_failed')
        receipt = settlement_receipt(result, state, payer)
    except Exception:
        return unavailable('settlement_unavailable')
    x402_usage.finish(usage, payment_id, 'settled')
    response.headers.update({**HEADERS, 'PAYMENT-RESPONSE': encoded_json(receipt), 'X-PAYMENT-RESPONSE': encoded_json(receipt)})
    return response


async def paid_bars(request: web.Request) -> web.Response:
    state = request.app[STATE]
    if not fresh(state['config']):
        return unavailable('manifest_expired')
    modern = request.headers.getall('PAYMENT-SIGNATURE', [])
    legacy = request.headers.getall('X-PAYMENT', [])
    if len(modern) > 1 or len(legacy) > 1 or (modern and legacy and modern != legacy):
        return payment_required(state, 'invalid_payment')
    if not modern and not legacy:
        return payment_required(state)
    day = x402_usage.request_day(request['meter_timestamp'] // 1000)
    async with state['lock']:
        if state['failed']:
            return unavailable('local_record_unavailable')
        return await settle_bars(request, state, (modern or legacy)[0], day)


async def manifest_response(request: web.Request) -> web.Response:
    config = request.app[STATE]['config']
    if not fresh(config):
        return unavailable('manifest_expired')
    return web.Response(body=config['manifest_bytes'], content_type='application/json',
                        headers={**HEADERS, 'X-TraderSwift-Manifest-Signature': config['signature_header']})


async def preflight(request: web.Request) -> web.Response:
    return web.Response(status=204, headers={**HEADERS, 'Access-Control-Allow-Methods': 'GET, OPTIONS',
                                            'Access-Control-Allow-Headers': 'X-PAYMENT, PAYMENT-SIGNATURE'})


@web.middleware
async def audit_requests(request, handler):
    state = request.app[STATE]
    request['meter_timestamp'] = int(time.time() * 1000)
    try:
        response = await handler(request)
    except web.HTTPException as error:
        response = web.json_response({'error': 'request_refused'}, status=error.status, headers=HEADERS)
    except Exception:
        state['failed'] = True
        response = unavailable('local_record_unavailable')
    try:
        x402_usage.meter_request(state['usage'], state['config'], request, response, request['meter_timestamp'])
    except Exception:
        state['failed'] = True
        response = unavailable('meter_unavailable')
    return response


async def usage_lifecycle(app):
    state = app[STATE]
    state['usage'] = x402_usage.open_usage(state['config'])
    try:
        yield
    finally:
        state['usage']['ledger'].close()
        state['usage']['meter'].close()


def install(app: web.Application, config: dict) -> None:
    app[STATE] = {'config': config, 'requirements': requirements(config), 'lock': asyncio.Lock(), 'failed': False,
                  'resource': {'url': config['origin'] + '/paid/v1/bars',
                               'description': 'Time series bars', 'mimeType': 'application/json'}}
    app.middlewares.append(audit_requests)
    app.cleanup_ctx.append(usage_lifecycle)
    app.router.add_get('/paid/v1/bars', paid_bars, allow_head=False)
    app.router.add_options('/paid/v1/bars', preflight)
    app.router.add_get('/.well-known/traderswift-manifest.json', manifest_response, allow_head=False)
