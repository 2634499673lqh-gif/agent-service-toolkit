from datetime import date
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from .aoi import AOI
from .models import Period

STAC_ENDPOINT = "https://earth-search.aws.element84.com/v1/search"
STAC_COLLECTION = "sentinel-2-l2a"


class Sentinel2Item(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    item_id: str = Field(min_length=1, max_length=160)
    acquisition_date: date
    cloud_cover: float = Field(ge=0, le=100)
    collection: str = STAC_COLLECTION
    red_asset: str = Field(min_length=1, max_length=1000)
    nir_asset: str = Field(min_length=1, max_length=1000)
    endpoint: str = STAC_ENDPOINT
    query_start: date
    query_end: date


def _item_from_feature(feature: dict[str, Any], period: Period) -> Sentinel2Item:
    properties = feature.get("properties") or {}
    assets = feature.get("assets") or {}
    red = assets.get("red", {}).get("href")
    nir = assets.get("nir", {}).get("href")
    if not red:
        raise ValueError("selected item is missing Red asset")
    if not nir:
        raise ValueError("selected item is missing NIR asset")
    timestamp = str(properties.get("datetime", ""))[:10]
    if not timestamp:
        raise ValueError("selected item is missing acquisition date")
    cloud = properties.get("eo:cloud_cover")
    if not isinstance(cloud, (int, float)):
        raise ValueError("selected item is missing cloud cover")
    return Sentinel2Item(
        item_id=str(feature.get("id", "")),
        acquisition_date=date.fromisoformat(timestamp),
        cloud_cover=float(cloud),
        collection=str(feature.get("collection", "")),
        red_asset=str(red),
        nir_asset=str(nir),
        query_start=period.start,
        query_end=period.end,
    )


def select_sentinel2(features: list[dict[str, Any]], period: Period, cloud_threshold: float) -> Sentinel2Item:
    candidates: list[Sentinel2Item] = []
    for feature in features:
        try:
            item = _item_from_feature(feature, period)
        except (TypeError, ValueError):
            continue
        if item.collection == STAC_COLLECTION and period.start <= item.acquisition_date <= period.end and item.cloud_cover <= cloud_threshold:
            candidates.append(item)
    if not candidates:
        raise ValueError("no Sentinel-2 item satisfies the cloud threshold")
    return min(candidates, key=lambda item: (item.cloud_cover, item.acquisition_date, item.item_id))


def search_sentinel2(
    aoi: AOI,
    period: Period,
    cloud_threshold: float = 30.0,
    *,
    limit: int = 10,
    client: httpx.Client | None = None,
) -> Sentinel2Item:
    if not 1 <= limit <= 10:
        raise ValueError("limit must be between 1 and 10")
    params = {
        "collections": STAC_COLLECTION,
        "bbox": ",".join(str(value) for value in aoi.bbox),
        "datetime": f"{period.start.isoformat()}T00:00:00Z/{period.end.isoformat()}T23:59:59Z",
        "limit": str(limit),
    }
    own_client = client is None
    http = client or httpx.Client(timeout=15.0)
    try:
        response = http.get(STAC_ENDPOINT, params=params)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ConnectionError("Sentinel-2 metadata search failed") from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError("malformed STAC response") from exc
    finally:
        if own_client:
            http.close()
    features = payload.get("features") if isinstance(payload, dict) else None
    if not isinstance(features, list):
        raise ValueError("invalid STAC response")
    return select_sentinel2(features, period, cloud_threshold)


__all__ = ["STAC_COLLECTION", "STAC_ENDPOINT", "Sentinel2Item", "search_sentinel2", "select_sentinel2"]
