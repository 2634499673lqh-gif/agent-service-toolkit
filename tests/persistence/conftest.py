import asyncio
import sys


def pytest_asyncio_loop_factories(config, item):
    """Use psycopg's Selector loop on Windows; preserve the native policy elsewhere."""
    if sys.platform == "win32":
        return {"selector": asyncio.SelectorEventLoop}
    return {"default": asyncio.get_event_loop_policy().new_event_loop}
