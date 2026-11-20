# Run a Time series node

A Time series node keeps market candles on your own machine and serves them to anyone who asks, over plain HTTP. Running one is for operators; you do not need to run one to use the data.

**Just want to see it? Start here:** read what a node answers in [the data interface](DATA_INTERFACE.md): the three public GET routes, with example requests and responses, need no install and no account. A public test feed on TraderSwift's own node is not open yet; until it is, the interface page is the no-install way to see the data. Everything below is for running your own node.

**What you need to run one:** a Linux machine and Python 3.11 or newer. No private access, invitation or account is needed for a free node. A paid node needs your own Base payee address and a signed manifest, covered further down.

A node stores venue candles in local SQLite and serves the versioned HTTP data
interface. This process does both `store` and `serve` work. Charter v1 covers
Hyperliquid `1h` candles for BNB, BTC, DOGE, ETH, HYPE, SOL and XRP. Each node
ingests independently; it does not copy another node's history. It is a data
service, not a chain validator or a wallet.

You need Linux, system Python 3.11 or newer, writable disk, and the packages in
`requirements.txt` already installed for that Python. `aiohttp` handles HTTP and
websockets; `cryptography` verifies manifests and signs CDP authentication JWTs.
Provision those dependencies through your system's package process before this
offline installation. No virtual environment, package download, Redis, build
step or JavaScript runtime is needed. Venue HTTP and websocket access is needed
when running. Initial HTTP readiness waits for backfill; available history is
limited by the venue. Keep enough disk for the database and growing local logs.

## Install

Start in an unpacked checkout, with no existing `runtime/` directory. In
`config.example.json`, replace `http_base_url` and `websocket_base_url` with the
venue's base URLs (without `/info` or `/ws`), and choose `listen_port`. The shipped
`.invalid` domains and `/path/to/...` locations are deliberately fake. Loopback
is the initial listener; a reverse proxy can expose it over your HTTPS origin.

The combined example documents serve, anchor and paid settings in one file.
The following install creates the single active config, `runtime/node.json`,
with paid mode omitted for a free-feed first start. This is necessary because
paid mode requires your valid signed manifest; placeholder signatures and
addresses cannot pass validation. Keep the example as the template for enabling
paid mode later. No authentication file is needed for the free feed.

```sh
python3 -B - <<'PY'
import sys
from importlib.metadata import version

import aiohttp
import cryptography

assert sys.version_info >= (3, 11), 'Python 3.11 or newer required'
assert (3, 9) <= tuple(map(int, version('aiohttp').split('.')[:2])) < (4, 0)
assert 43 <= int(version('cryptography').split('.')[0]) < 47
PY
mkdir runtime
cp ./*.py requirements.txt config.example.json runtime/
python3 -B - <<'PY'
import json
from pathlib import Path

config = json.loads(Path('runtime/config.example.json').read_text())
config.pop('paid')
Path('runtime/node.json').write_text(json.dumps(config, indent=2) + '\n')
Path('runtime/node.json').chmod(0o600)
PY
```

## Start

From the checkout, run this one command:

```sh
python3 -B runtime/node.py serve --config runtime/node.json
```

Wait for `node ready`. A GET to your listener's `/public/v1/instruments` should
return HTTP 200, version `1`, and seven instruments. Ctrl-C stops cleanly.
Startup failure exits nonzero; check configuration, dependency versions and
venue availability. `node.sqlite3` resolves beside `node.json`, independent of
the shell's working directory. Do not run two processes against the same state.

## Keep it running with a user unit

Stop the foreground process first. Copy `deploy/timeseries-node.service` into
your user's systemd unit directory, normally `~/.config/systemd/user/`. Replace
every `/path/to/checkout/runtime` in that copy with the absolute runtime path.
Check that `/usr/bin/python3` has the required packages. There are no credentials
or environment variables in the unit. Run `systemd-analyze verify --user` on
the edited file, then `systemctl --user daemon-reload` and
`systemctl --user enable --now timeseries-node.service`.

Use `systemctl --user status timeseries-node.service` and
`journalctl --user -u timeseries-node.service -n 50` to inspect it;
`systemctl --user stop timeseries-node.service` stops it. `Restart=on-failure`
recovers failed processes. Login-session persistence depends on your user
manager; an administrator may need to enable lingering for boot/logout
operation. After editing the active config or replacing a manifest, use
`systemctl --user restart timeseries-node.service`. Preserve the SQLite file,
its WAL state and both paid logs when upgrading; stop before taking a file copy.

## Register as a provider

Registration is separate from starting the process. Using your chain client or
wallet, inspect `/network/v1/charter/timeseries`: it must be live, unvetoed and
allow `store` and `serve`. Your hub `account_id` must exist and have a claimed
owner (`RegisterAccount` then `ClaimOwner` if needed). Inspect
`/network/v1/account/<account_id>` to confirm the claim.

Register each kind separately, with distinct provider IDs and provider signing
addresses: the chain refuses an address already registered in the same subnet.
The owner signs this exact text with EIP-191 `personal_sign`, substituting the
values, using newline separators and **no trailing newline**:

```text
traderswift register provider
chain id: <chain_id>
subnet: timeseries
provider id: <provider_id>
provider kind: <store_or_serve>
provider address: <provider_address>
endpoint: <endpoint>
```

Submit `/traderswift.network.v1.MsgRegisterProvider` with all eight proto fields:
`submitter`, `subnet`, `provider_id`, `provider_kind`, `provider_address`,
`account_id`, `endpoint`, `owner_signature`. The owner signature is 65 bytes
encoded as 130 lowercase hex characters. The endpoint may be empty, but its
signed text must exactly match the message. The transaction signer `submitter`
is the provider address itself or a charter operator. That transaction signature
does not replace the account owner's signature in `owner_signature`.

After inclusion, inspect `/network/v1/provider/timeseries/<provider_id>` for
the recorded address, endpoint, `endorsed` and `vetoed`. With `join: open`,
registration endorses immediately; with `join: endorsed`, a charter operator or
an endorsed, unvetoed provider must submit `MsgEndorseProvider`. Registration
alone does not make anchors count toward agreement. Use the registered store
ID in `store_provider_id`, and serve ID in `paid.serve_provider_id`.

This procedure follows the chain README's owner signing text and
`network.proto` message fields. CLI registration flag names are not established
by those references; use a client that constructs that message rather than
assuming a registration command exists. Keys remain in the operator's wallet
or chain keyring; the node receives only paths and signer names.

## Anchor daily data

Fill the `chain_*` fields in the active config from your chain deployment.
`chain_key_name` must sign as the registered store address. Use a preprovisioned
client and keyring that can sign noninteractively: stdin is disconnected. The
example chooses the `file` backend; ensure its unlocking mechanism works with
your client. The sample backend is not proof that unattended signing works.

Replace `serve` with `anchor` in the start command or unit's `ExecStart`; the
same process still ingests and serves, and also serves paid requests when
configured. Do not start a second process. At or after 02:00 UTC the next
nonempty candle event anchors the previous UTC day and older unattempted days.
Backfill, heartbeats and passage of time alone do not submit transactions.

Inspect `/public/v1/segments?day=YYYY-MM-DD` for the day's `candle_root`, and
`/network/v1/anchors/timeseries/candle_root/YYYY-MM-DD` on chain REST for
published anchors. The SQLite `anchor_submissions` table records `submitted`,
`uncertain`, `confirmed` or `conflict`. `submitted` means broadcast acceptance;
chain REST confirmation establishes inclusion. Never clear an uncertain entry
without checking the chain outcome. Two distinct endorsed, unvetoed stores
with the same hash establish agreement. See [ANCHOR.md](ANCHOR.md) for the
`verify` command, its separate read-only config, and recovery semantics.

UNVERIFIED: unattended signing on a live chain, inclusion, and live chain
integration. The client flags were checked against the `traderswiftd` help text. The node does not anchor meters or settle fee splits.

## Enable paid bars and read the meter

Copy the entire `paid` object from the combined example into `runtime/node.json`,
then replace every placeholder before restarting. Set your public HTTPS origin,
registered serve ID, actual CDP facilitator base URL, CDP key ID and external
key file path, and your own Base receive address in `payTo`. The file must be
outside the installed source directory and readable only as needed by the
service user. No Base payee private key is needed. Do not copy authentication
contents into JSON, source, unit files or logs. Legacy `credential_file` is for
fake checks; do not combine it with the two CDP fields.

Choose `price_usdc` as a decimal string and `daily_cap` as an integer; the
illustrative values are `0.01` USDC and 100 requests per payer per UTC day.
These are explicit config values, not built-in commercial defaults. Pending
and settled reservations count; the next request at the cap returns 429 before
settlement or delivery. Midnight is evaluated on requests, without a timer.
Supply the public manifest, detached envelope and matching public pin described
below. All configured file paths must be distinct. Relative paths resolve beside
the config. Paid startup fails closed if artifacts are absent or invalid.

`meter_file` is JSON Lines in `rec.v1` shape, one record per application request
while paid mode is enabled, including free requests and errors. Read it with
`tail -n 20 /path/to/operator-data/meter.jsonl` or parse each line as JSON.
Fields include `event_id`, request-arrival `ts` in milliseconds, `node_id`,
`resource`, `status`, `kind` and `bytes`, all strings. Successful bar or segment
GETs use `kind: bytes` and name `store_provider_id` and `serve_provider_id`;
other records use `kind: view`. Byte counts measure prepared response bodies,
not confirmed delivery. Free-only mode has no request meter.

The separate `ledger_file` preserves replay protection and daily caps; it is
not the meter. Keep it across restarts, run one process per ledger, and review
ambiguous settlements before any manual reconciliation. See [x402.md](../x402.md)
for failure behavior and exact fields. Live CDP acceptance and Base settlement
remain UNVERIFIED; a local config check establishes neither.

## Point a chart front end at the node

Configure the front end's data origin as your node's HTTPS origin. Fetch
`/public/v1/instruments` and read its `instruments` array. Fetch
`/public/v1/bars?venue=hyperliquid&symbol=BTC&tf=1h&bars=300` for bars; the
response has `bars` and provenance. Prices and volumes are decimal strings;
preserve them for exact calculations and hashing. Convert only at a plotting
library boundary if that library needs numbers. Public GETs allow cross-origin
access with `Access-Control-Allow-Origin: *`.

Refresh on user navigation or explicit refresh events. The standalone node has
no public live stream, quotes, drawing persistence, or static file server.
The bundled `web/` chart currently expects same-origin `/api/instruments`,
`/api/bars`, `/api/live`, `/api/quotes` and `/api/tv_drawings`; simply serving
`tv.html` beside this node is insufficient. Adapt its data calls and instruments
envelope, disable unsupported features, and host its assets separately, or use
a front end already supporting the public v1 interface. See
[DATA_INTERFACE.md](DATA_INTERFACE.md) for response shapes.

A paid front end uses `/paid/v1/bars`, reviews the 402 offer through the wallet,
and sends the wallet-approved `PAYMENT-SIGNATURE`; it must verify the manifest
and offer before requesting a signature. Retain the returned payment receipt.
Configure your proxy to preserve payment and manifest headers and avoid caching
paid responses. Public routes remain free when paid mode is enabled.

## Get listed with a signed manifest

Provider discovery starts at `/network/v1/providers/timeseries`. A reachable
endpoint and registration are prerequisites, not a promise of directory or
wallet acceptance. For a commercial listing, prepare the closed
`traderswift.frontend-manifest.v1` public schema described in [x402.md](../x402.md):
identity, provider, origin, API versions, scopes, licence, payee, network, asset,
atomic amount, terms, price version, validity, facilitator URLs and refund/dispute
contact. All manifest integers are strings. This node accepts `timeseries`,
`traderswift-1`, API `2`, one configured scope, per-use terms with duration `0`,
and validity no longer than seven days. Its provider ID is the configured serve
ID. Payee, price, origin, scope and facilitator must exactly match the config.

Canonicalize unsigned JSON by recursively sorting keys in UTF-16 order, retaining
array order, and emitting compact UTF-8 without BOM or trailing newline. Hash
those bytes with SHA-256. Externally sign `traderswift frontend manifest v1`
plus a newline plus those bytes through Cosmos ADR-036 `sign/MsgSignData`, using
the provider signer. The sign document has empty chain ID and memo, account
number and sequence `0`, fee amount `[]`, gas `0`, and base64 data. Supply a
detached `{kid, address, pub_key: {type, value}, signature}` envelope: compressed
secp256k1 public key and compact low-S signature, both base64. Configure
`manifest_pin` with that trusted `kid`, `type`, `address`, `publicKey`; do not
obtain a trust pin from an untrusted manifest response.

The node serves exact bytes at `/.well-known/traderswift-manifest.json`, with the
envelope in `X-TraderSwift-Manifest-Signature` as unpadded base64url. A wallet
fetches without credentials, redirects or cache, limits the body to 64 KiB,
checks the signature/public-key binding, and compares the offer to the manifest.
Renew before expiry, increase the manifest version for changes, and restart
after replacing artifacts; the node loads them at startup and rechecks expiry
on requests. A changed offer needs fresh wallet review.

For hub-backed listing, first establish that the live charter permits the
appropriate `frontend` kind and manifest segment kind. Publish the canonical
hash using a provider-signed `MsgAnchor` under that charter's version, freshness
and revocation rules, then provide the origin, registration and commitment to
the directory or wallet maintainer. The frontend charter proposal is
not evidence of deployment; no universal listing CLI or automatic listing is
implemented here. Wallets must check fresh endorsement/veto state and pin both
manifest version and hash through purchase. A hash commitment proves publication,
not delivery or truth of the offer.

UNVERIFIED: the node currently derives `cosmos` manifest addresses, whereas the
chain reference specifies the `traderswift` prefix. Confirm same-key address
binding in the wallet/chain integration before public listing; do not assume
the local manifest check proves provider registration. Live charter activation,
directory acceptance and wallet acceptance also remain integration work.
