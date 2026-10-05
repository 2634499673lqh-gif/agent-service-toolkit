"""Stable, sanitized GeoChange product endpoints."""

from fastapi import APIRouter

from schema.geochange_api import (
    GeoChangeCapabilitiesResponse,
    GeoChangeCapability,
)
from service.auth_dependency import PrincipalDependency

geochange_router = APIRouter(prefix="/api/v1/geochange", tags=["geochange"])

_LIMITS = {
    "NDVI": "NDVI 是植被指数变化，不等同于植被面积变化。",
    "NDWI": "NDWI 是连续水体相关指数，不足以确认水域面积或扩张。",
    "NDBI": "NDBI 是连续建成区相关指数，不足以确认建设用地或城市扩张面积。",
}


@geochange_router.get("/capabilities", response_model=GeoChangeCapabilitiesResponse)
async def capabilities(principal: PrincipalDependency) -> GeoChangeCapabilitiesResponse:
    del principal
    return GeoChangeCapabilitiesResponse(
        capabilities=[
            GeoChangeCapability(
                indicator="NDVI",
                label="植被指数变化",
                analysis_type="vegetation_change",
                periods=["2023-07", "2024-07"],
                data_source="已验证的 Sentinel-2 缓存样例",
                scientific_limit=_LIMITS["NDVI"],
            ),
            GeoChangeCapability(
                indicator="NDWI",
                label="水体相关指数变化",
                analysis_type="water_change",
                periods=["2023-07", "2024-07"],
                data_source="已验证的 Sentinel-2 缓存样例",
                scientific_limit=_LIMITS["NDWI"],
            ),
            GeoChangeCapability(
                indicator="NDBI",
                label="建成区相关指数变化",
                analysis_type="urban_change",
                periods=["2023-07", "2024-07"],
                data_source="已验证的 Sentinel-2 缓存样例",
                scientific_limit=_LIMITS["NDBI"],
            ),
        ],
        aoi_label="武汉东湖（受限缓存覆盖区）",
        data_availability="当前仅支持已验证缓存的武汉东湖样例时段。",
    )
