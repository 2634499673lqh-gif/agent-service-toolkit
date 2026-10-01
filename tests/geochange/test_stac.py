import httpx
import pytest

from geochange.aoi import resolve_aoi
from geochange.models import Period
from geochange.stac import search_sentinel2


def _period():
    return Period(start="2023-07-01", end="2023-07-31")


def test_malformed_stac_json_is_terminal_value_error():
    class MalformedResponse:
        def raise_for_status(self):
            return None

        def json(self):
            raise ValueError("invalid json")

    class MalformedClient:
        def get(self, *_args, **_kwargs):
            return MalformedResponse()

    with pytest.raises(ValueError, match="malformed STAC response"):
        search_sentinel2(resolve_aoi("wuhan_east_lake"), _period(), client=MalformedClient())


def test_provider_connectivity_failure_remains_connection_error():
    class NetworkClient:
        def get(self, *_args, **_kwargs):
            raise httpx.ConnectError("offline")

    with pytest.raises(ConnectionError):
        search_sentinel2(resolve_aoi("wuhan_east_lake"), _period(), client=NetworkClient())
