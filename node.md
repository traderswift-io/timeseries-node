# HTTP error boundary

aiohttp rejects malformed headers before application middleware runs. Its default
protocol error handler can log the rejected header and echo it in a response.

At startup, `node.py` replaces `RequestHandler.handle_error` with an empty,
connection-closing response that preserves the error status. If a response has
already started, it raises a fixed connection error instead. Unexpected failures
emit one line to stderr: `node http server failure status=500`, with the status
replaced by the boundary's HTTP status (for example, 504 for a timeout).
Parser rejections below 500 remain silent.

`RequestHandler.log_exception` also emits the fixed line with status 500 for
aiohttp failures outside the error-response path, including missing or invalid
handler responses. Both hooks discard exception text, logging arguments, headers,
paths and queries. The dedicated disabled server logger suppresses aiohttp's
remaining raw diagnostics; access logging remains disabled.

The replacement applies to aiohttp servers in this process, which runs one node
listener. This protocol hook must be checked when updating aiohttp. Application
responses, bar serialization and hashing are unchanged.
