import asyncio
import sys


def pytest_asyncio_loop_factories(config, item):
    """Use psycopg-compatible selector loops for Windows runtime tests."""

    del config, item
    if sys.platform == "win32":
        return {"selector": asyncio.SelectorEventLoop}
    return {"default": asyncio.get_event_loop_policy().new_event_loop}
