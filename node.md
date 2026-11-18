# HTTP error boundary

aiohttp rejects malformed headers before application middleware runs. Its default
protocol error handler can log the rejected header and echo it in a response.

At startup, `node.py` replaces `RequestHandler.handle_error` with an empty,
connection-closing response that preserves the error status. If a response has
already started, it raises a fixed connection error instead. The dedicated
disabled server logger also suppresses other aiohttp server exception logging;
access logging remains disabled.

The replacement applies to aiohttp servers in this process, which runs one node
listener. This protocol hook must be checked when updating aiohttp. Application
responses, bar serialization and hashing are unchanged.
