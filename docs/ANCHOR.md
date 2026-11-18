# Daily anchors and verification

Run `python3 -B node.py anchor --config node.json` to ingest and serve with daily anchoring enabled. This replaces `serve` for that process. It uses the same database and venue configuration described in [NODE.md](NODE.md), plus these JSON fields:

| Field | Meaning |
|---|---|
| `store_provider_id` | Registered `store` provider identifier, distinct from its signing address and the node's display name |
| `chain_binary` | Path to the chain client executable |
| `chain_keyring_dir` | Directory passed to the client's `--keyring-dir` flag |
| `chain_keyring_backend` | `test` (default), `file` or `os`, passed to `--keyring-backend`; the client's own default is `os`, which has no keyring on a server |
| `chain_key_name` | Client key name passed with `--from`; its address must be the registered store provider's address |
| `chain_id` | Chain identifier passed with `--chain-id` |
| `chain_rpc_url` | Client RPC base URL passed with `--node` |
| `chain_rest_base_url` | HTTP base URL for the chain's Anchors REST route |

Relative binary and keyring paths resolve beside the JSON file. The program does not inspect the keyring directory or obtain signing material. It passes the directory and key name to the client as separate arguments without a shell. Client stdin is disconnected; the configured client must already support noninteractive signing. Client stderr is discarded and stdout is parsed only for the result code and transaction hash. Neither stream, the input, nor the process environment is printed or stored. The node persists only the day, root, chain identifier, provider identifier and submission status.

Every nonempty venue candle message is fully stored before triggering anchoring. The receipt time, rather than the candle's open time, determines settlement. At or after 02:00 UTC, the next candle event submits the preceding day and any older stored days that have not been attempted. Backfill alone, subscription acknowledgements, heartbeats and passage of time do not submit transactions. Empty days are skipped. The root uses the existing v1 hash arithmetic and charter coverage, including omission of empty segments. An event containing corrections is stored before hashing; later corrections cannot replace an immutable chain anchor.

The command is `tx network anchor --subnet timeseries --segment-kind candle_root --segment-id DAY --segment-hash ROOT --provider-id STORE --from KEY --keyring-dir DIR --keyring-backend BACKEND --chain-id CHAIN --node RPC --output json --yes --broadcast-mode sync`. The chain CLI example establishes the anchor subcommand, message flags, `--from` and `--chain-id`. The available references do not establish `--keyring-dir`, `--output`, `--yes` or `--broadcast-mode sync` support for this build; those flags remain UNVERIFIED against the actual binary. `--node` is shown for other chain commands. No real chain execution is needed for the fake checks.

Before submitting, the node reads `/network/v1/anchors/timeseries/candle_root/DAY`. An existing own-provider anchor is recorded as `confirmed` if its hash matches and it is not vetoed, otherwise `conflict`. A REST failure leaves the day available for a later candle event. A new attempt is recorded as `uncertain` before the client starts. A successful client exit with JSON code zero and a valid transaction hash changes it to `submitted`, which means broadcast acceptance, not inclusion. Subsequent bar events reconcile `submitted` or `uncertain` records against REST without resubmitting. Confirmed and conflicting records are retained across restarts.

Client failure, rejection, timeout, interruption or a crash between recording and broadcasting can leave an uncertain record. This deliberately requires operator review: check the chain outcome before explicitly removing that day's local `anchor_submissions` record to permit another attempt. Do not discard this ledger blindly. No automatic timed retries or transaction confirmation polling run. Each external request and client invocation has a 60-second operation deadline. Anchoring is sequential with ingestion, so a slow client can delay processing buffered candles. The chain enforces registration, the live charter, signer authorization and veto rules; configuration must identify a store provider.

Run `python3 -B node.py verify --config verify.json --day YYYY-MM-DD`. Verification requires only `chain_rest_base_url` and `node_base_url` in its JSON file. The node URL must be the server origin; the command fetches `/segments?day=YYYY-MM-DD`. Both `/segments` and `/public/v1/segments` serve the same response.

Verification recomputes the root from the returned segment list, ignoring its advertised `candle_root`. It validates the day, charter, coverage, uniqueness and hash format. It compares against the chain REST route's bare JSON array of anchors. Duplicate providers and malformed responses are errors, rather than additional votes. Only endorsed, non-vetoed anchors count. The JSON result is `agree` when at least two distinct providers match the recomputed root, `short` with fewer than two eligible providers, and `disagree` otherwise. A matching pair wins even if a third differs. These are successful reports with exit status zero; configuration, transport and malformed-response errors exit nonzero. This verifies consistency of published segment hashes, not the underlying venue bars or provider registration kinds independently.
