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
                label="Landsat 植被指数变化",
                analysis_type="vegetation_change",
                periods=["2023-07", "2024-07"],
                data_source="Landsat 8/9 Collection 2 Level-2",
                scientific_limit=_LIMITS["NDVI"],
                aoi_id="jianghan_district_420103",
                aoi_label="武汉市江汉区",
                data_mode="real_stac_landsat_local",
                quick_action="比较武汉市江汉区 2023 年 7 月和 2024 年 7 月的 NDVI 变化",
            ),
            GeoChangeCapability(
                indicator="NDVI",
                label="植被指数变化",
                analysis_type="vegetation_change",
                periods=["2023-07", "2024-07"],
                data_source="已验证的 Sentinel-2 缓存样例",
                scientific_limit=_LIMITS["NDVI"],
                quick_action="比较武汉东湖 2023年7月和2024年7月的 NDVI 变化",
            ),
            GeoChangeCapability(
                indicator="NDWI",
                label="水体相关指数变化",
                analysis_type="water_change",
                periods=["2023-07", "2024-07"],
                data_source="已验证的 Sentinel-2 缓存样例",
                scientific_limit=_LIMITS["NDWI"],
                quick_action="比较武汉东湖 2023年7月和2024年7月的 NDWI 变化",
            ),
            GeoChangeCapability(
                indicator="NDBI",
                label="建成区相关指数变化",
                analysis_type="urban_change",
                periods=["2023-07", "2024-07"],
                data_source="已验证的 Sentinel-2 缓存样例",
                scientific_limit=_LIMITS["NDBI"],
                quick_action="比较武汉东湖 2023年7月和2024年7月的 NDBI 变化",
            ),
        ],
        aoi_label="武汉市江汉区 / 武汉东湖（能力按区域区分）",
        data_availability="江汉区支持 Landsat NDVI 完整月比较（2023–2025）；东湖支持已验证 Sentinel-2 缓存样例时段。其他区域尚不支持。",
    )
