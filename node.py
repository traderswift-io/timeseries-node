import argparse
import asyncio
import signal
import sys
from logging import Logger
from pathlib import Path

from aiohttp import ClientSession, ClientTimeout, web
from aiohttp.web_protocol import RequestHandler

import anchoring
import configuration
import public_feed
import store
import venue
import verification
import x402
import x402_configuration


def server_error_response(handler, request, status=500, exc=None, message=None):
    if request.writer.output_size > 0:
        raise ConnectionError('response already started')
    response = web.Response(status=status, body=b'')
    response.force_close()
    return response


async def run_node(config: dict, anchor: bool = False) -> None:
    paid = x402_configuration.load_paid(config, config['config_path'])
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stopped.set)
    connection = store.open_store(config['database'])
    if anchor:
        anchoring.prepare_store(connection)
    app = public_feed.create_app(connection, config['node_id'])
    if paid is not None:
        x402.install(app, paid)
    server_logger = Logger('node.http')
    server_logger.disabled = True
    RequestHandler.handle_error = server_error_response
    runner = web.AppRunner(app, access_log=None, logger=server_logger, max_field_size=16384)
    tasks = []
    websocket = None
    try:
        async with ClientSession(timeout=ClientTimeout(total=60)) as session:
            async def on_bar_event(now_seconds: int) -> None:
                await anchoring.on_bar_event(session, config, connection, now_seconds)

            try:
                websocket = await venue.open_stream(session, config)
                await venue.backfill(session, config, connection)
                await runner.setup()
                await web.TCPSite(runner, config['listen_host'], config['listen_port']).start()
                tasks = [asyncio.create_task(venue.extend(session, config, connection, websocket,
                                                         on_bar_event if anchor else None)),
                         asyncio.create_task(stopped.wait())]
                print('node ready', flush=True)
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                if websocket is not None:
                    await websocket.close()
    finally:
        await runner.cleanup()
        connection.close()
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(signum)


def main() -> None:
    parser = argparse.ArgumentParser(description='Time series store and serve node')
    commands = parser.add_subparsers(dest='command', required=True)
    serve = commands.add_parser('serve', help='ingest venue candles and serve the public v1 interface')
    serve.add_argument('--config', required=True, type=Path)
    anchor = commands.add_parser('anchor', help='ingest, serve and anchor settled days on candle events')
    anchor.add_argument('--config', required=True, type=Path)
    verify = commands.add_parser('verify', help='compare a node day with endorsed chain anchors')
    verify.add_argument('--config', required=True, type=Path)
    verify.add_argument('--day', required=True)
    arguments = parser.parse_args()
    try:
        config = configuration.load_config(arguments.config, arguments.command)
        if arguments.command == 'verify':
            asyncio.run(verification.print_verification(config, arguments.day))
        else:
            asyncio.run(run_node(config, anchor=arguments.command == 'anchor'))
    except Exception as error:
        message = 'node command failed; check configuration and service availability'
        if isinstance(error, ValueError) and str(error) == 'paid route startup refused: invalid or missing configuration':
            message = str(error)
        print(message, file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
