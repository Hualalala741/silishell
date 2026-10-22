from __future__ import annotations

import numpy as np


def build_abandonment_mask(
    historical: np.ndarray,
    recent_layers: list[np.ndarray],
    valid_data: np.ndarray,
    historical_crop_values: list[int],
    recent_proxy_values: list[int],
) -> np.ndarray:
    """Historical crop pixels that persist as low-use proxy classes in every recent year."""
    if not recent_layers:
        raise ValueError("至少需要一个近年土地覆盖图层")
    historical_crop = np.isin(historical, historical_crop_values)
    persistently_non_crop = np.ones(historical.shape, dtype=bool)
    for recent in recent_layers:
        if recent.shape != historical.shape:
            raise ValueError("年度土地覆盖图层尺寸不一致")
        persistently_non_crop &= np.isin(recent, recent_proxy_values)
    return historical_crop & persistently_non_crop & valid_data


def build_stable_natural_resource_mask(
    annual_layers: list[np.ndarray],
    valid_data: np.ndarray,
    resource_values: list[int],
) -> np.ndarray:
    """Pixels that stay in bare/rangeland proxy classes for every supplied year."""
    if not annual_layers:
        raise ValueError("至少需要一个年度土地覆盖图层")
    stable_resource = np.ones(annual_layers[0].shape, dtype=bool)
    for layer in annual_layers:
        if layer.shape != annual_layers[0].shape:
            raise ValueError("年度土地覆盖图层尺寸不一致")
        stable_resource &= np.isin(layer, resource_values)
    return stable_resource & valid_data
