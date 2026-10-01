from pydantic import BaseModel, ConfigDict


class AOI(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    catalog_key: str
    canonical_name: str
    bbox: tuple[float, float, float, float]
    geometry: dict[str, object]
    crs: str = "EPSG:4326"
    source: str = "taskpilot.geochange.catalog.v1"

    def _validate_bbox(self) -> None:
        west, south, east, north = self.bbox
        if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
            raise ValueError("AOI bbox is invalid")
        if (east - west) * (north - south) > 1.0:
            raise ValueError("AOI is larger than the bounded MVP area")


_EAST_LAKE = AOI(
    catalog_key="wuhan_east_lake",
    canonical_name="Wuhan East Lake",
    bbox=(114.30, 30.50, 114.45, 30.62),
    geometry={
        "type": "Polygon",
        "coordinates": [
            [[114.30, 30.50], [114.45, 30.50], [114.45, 30.62], [114.30, 30.62], [114.30, 30.50]]
        ],
    },
)
_EAST_LAKE._validate_bbox()


def resolve_aoi(place: str) -> AOI:
    """Resolve only the controlled catalog entry; never geocode arbitrary input."""

    normalized = " ".join(place.casefold().replace("_", " ").split())
    if normalized not in {"wuhan east lake", "东湖", "武汉东湖"}:
        raise ValueError("unsupported AOI")
    return _EAST_LAKE


__all__ = ["AOI", "resolve_aoi"]
