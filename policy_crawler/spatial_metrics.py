from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.mask import mask
from shapely.geometry import mapping


METRIC_CRS = "EPSG:32650"


def nearest_distance_km(origins: gpd.GeoDataFrame, targets: gpd.GeoDataFrame) -> pd.Series:
    if targets.empty:
        return pd.Series(np.nan, index=origins.index, dtype=float)
    joined = gpd.sjoin_nearest(
        origins[["geometry"]],
        targets.to_crs(origins.crs)[["geometry"]],
        how="left",
        distance_col="distance_m",
    )
    return joined.groupby(joined.index)["distance_m"].min().reindex(origins.index) / 1000


def nearest_attribute(
    origins: gpd.GeoDataFrame, targets: gpd.GeoDataFrame, attribute: str
) -> pd.Series:
    if targets.empty:
        return pd.Series("unknown", index=origins.index, dtype=object)
    joined = gpd.sjoin_nearest(
        origins[["geometry"]],
        targets.to_crs(origins.crs)[[attribute, "geometry"]],
        how="left",
        distance_col="distance_m",
    )
    nearest = joined.sort_values("distance_m").groupby(joined.index)[attribute].first()
    return nearest.reindex(origins.index).fillna("unknown")


def nearby_count(
    origins: gpd.GeoDataFrame, targets: gpd.GeoDataFrame, radius_m: float
) -> pd.Series:
    if targets.empty:
        return pd.Series(0, index=origins.index, dtype=int)
    buffers = origins[["geometry"]].copy()
    buffers.geometry = buffers.geometry.buffer(radius_m)
    joined = gpd.sjoin(
        buffers,
        targets.to_crs(origins.crs)[["geometry"]],
        predicate="intersects",
        how="left",
    )
    return joined.groupby(joined.index)["index_right"].count().reindex(origins.index).fillna(0).astype(int)


def population_in_radius(origins, raster_path, radius_m: float = 5000) -> pd.Series:
    values: list[float] = []
    buffers = origins[["geometry"]].copy()
    buffers.geometry = buffers.geometry.buffer(radius_m)
    with rasterio.open(raster_path) as source:
        for geometry in buffers.to_crs(source.crs).geometry:
            data, _ = mask(source, [mapping(geometry)], crop=True, filled=False)
            valid = data[0].compressed().astype("float64")
            valid = valid[np.isfinite(valid) & (valid >= 0) & (valid < 1e10)]
            values.append(float(valid.sum(dtype="float64")) if len(valid) else 0.0)
    return pd.Series(values, index=origins.index)


def scale_score(values: pd.Series, higher_is_better: bool) -> pd.Series:
    numeric = values.astype(float)
    valid = numeric.dropna()
    if valid.empty or valid.max() == valid.min():
        return pd.Series(50.0, index=values.index)
    scaled = (numeric - valid.min()) / (valid.max() - valid.min()) * 100
    if not higher_is_better:
        scaled = 100 - scaled
    return scaled.fillna(50).clip(0, 100)
