# Standalone store and serve

The node runs both provider kinds in one Python process. It needs Python 3.11 or newer, aiohttp, and a writable local SQLite file. Dependencies are listed in `requirements.txt`. No Redis or PostgreSQL client is used.

Run `python3 -B node.py serve --config node.json`. Supply a JSON object with:

| Field | Meaning |
|---|---|
| `node_id` | Public name returned in provenance and segment responses |
| `database` | SQLite filename; relative paths resolve beside the config file |
| `http_base_url` | Venue HTTP base URL; the node appends `/info` |
| `websocket_base_url` | Venue websocket base URL; the node appends `/ws` |
| `listen_host` | Address on which to serve HTTP |
| `listen_port` | HTTP port, integer from 1 to 65535 |
| `backfill_days` | Initial history window, 1 to 200 days, default 30 |

There are no built-in venue or listen addresses. Supply public Hyperliquid base URLs for venue use, or local fake-venue URLs for checks. Free-only configuration requires no credentials. Optional [paid bars](../x402.md) add a signed operator manifest and an external facilitator authentication file (a CDP signing key or legacy Authorization value). Historical availability depends on what the venue retains; a requested window does not guarantee complete history.

`GET /public/v1/instruments`, `/public/v1/bars` and `/public/v1/segments?day=YYYY-MM-DD` use the existing [v1 interface](DATA_INTERFACE.md). Coverage is Hyperliquid hourly candles for BNB, BTC, DOGE, ETH, HYPE, SOL and XRP. The instruments response advertises that fixed coverage, including symbols for which the venue returned no history. All prices and volumes are decimal strings. The local SQLite database belongs to the existing `files` provenance category. `live_extended` is false because every returned candle has already been stored; there is no separate on-read live overlay. Status remains `live` until the hour ends, then `closed`. No candle is labelled validated.

The websocket subscribes before REST backfill begins, so updates arriving during startup remain buffered for ingestion. HTTP starts after backfill succeeds. REST uses bounded sequential windows. Restart and a clean websocket close trigger catch-up, including a two-day overlap to refresh recent candles. Subsequent writes happen only on venue websocket messages. There is no polling or scheduled hashing. A connection attempt, HTTP request or stream protocol failure exits the process with an error; there is no timed retry loop. An operator can restart the process to recover. SIGINT and SIGTERM stop the service and close its resources. A quiet connection has no application heartbeat in this first version; venue disconnect behavior needs live verification.

The node accepts the venue candle envelope `{"channel":"candle","data":{"s":"BTC","i":"1h","t":0,"o":"1","h":"1","l":"1","c":"1","v":"0"}}`, with data also allowed to be a list. Candle times must be aligned Unix milliseconds. Duplicate times replace the existing bar transactionally. Decimal values are stored as SQLite TEXT and read as Decimal, without arithmetic through floats.

`bars_v1.py` deliberately copies the reference's `Decimal(repr(float(value)))` conversion for serialization and hashing. This is a v1 compatibility constraint: it reproduces the reference's binary-float rounding even when the incoming decimal contains more precision. Changing it would change published hashes. `segments.py` copies the reference segment and root byte arithmetic. Rows are read in timestamp order; roots sort by venue, symbol and timeframe. Segment counts are strings, empty segments are omitted, and a fully empty day returns an empty root string, matching the current feed. The two-hour settlement boundary is evaluated on each read. Hashes are recomputed from stored bars so a correction changes the root immediately; no stale hash cache is retained.

`node.py` owns command dispatch and lifecycle; `configuration.py` loads configuration; `venue.py` ingests; `store.py` owns SQLite; `public_feed.py` serves; `bars_v1.py` and `segments.py` define serialization. The [anchor and verify commands](ANCHOR.md) add daily chain submission and agreement checking. Optional [paid bars and request metering](../x402.md) share this process. Service units and the operator kit remain separate work.
