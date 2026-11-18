import argparse
import asyncio
import signal
from pathlib import Path

from aiohttp import ClientSession, ClientTimeout, web

import configuration
import public_feed
import store
import venue


async def run_node(config: dict) -> None:
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stopped.set)
    connection = store.open_store(config['database'])
    runner = web.AppRunner(public_feed.create_app(connection, config['node_id']), access_log=None)
    tasks = []
    websocket = None
    try:
        async with ClientSession(timeout=ClientTimeout(total=60)) as session:
            try:
                websocket = await venue.open_stream(session, config)
                await venue.backfill(session, config, connection)
                await runner.setup()
                await web.TCPSite(runner, config['listen_host'], config['listen_port']).start()
                tasks = [asyncio.create_task(venue.extend(session, config, connection, websocket)),
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
    arguments = parser.parse_args()
    config = configuration.load_config(arguments.config)
    asyncio.run(run_node(config))


if __name__ == '__main__':
    main()
