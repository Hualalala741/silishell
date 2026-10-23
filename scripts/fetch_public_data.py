#!/usr/bin/env python3
"""Download and prepare the public geospatial inputs used by site selection.

Large source rasters stay out of Git. This script records the exact public
endpoints and produces the small Yongchun extracts consumed by the project.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import geopandas as gpd
import httpx
import rasterio
from rasterio.io import MemoryFile
from rasterio.mask import mask
from rasterio.merge import merge
from rasterio.warp import transform_geom
from shapely.geometry import LineString, Point, Polygon


ROOT = Path(__file__).resolve().parents[1]
BOUNDARY_API = "https://www.geoboundaries.org/api/current/gbOpen/CHN/ADM2/"
BOUNDARY_DIR = ROOT / "data" / "ods-source"
BOUNDARY_PATH = BOUNDARY_DIR / "geoBoundaries-CHN-ADM2.geojson"
BOUNDARY_METADATA_PATH = BOUNDARY_DIR / "geoboundaries-CHN-ADM2-metadata.json"
ADMIN_COMMIT = "e2c2b2e1fb4ce1fb76e879aa0b80e229adaf950c"
ADMIN_BASE = f"https://raw.githubusercontent.com/yescallop/areacodes/{ADMIN_COMMIT}"
ADMIN_DIR = ROOT / "data" / "raw" / "admin-codes" / "yescallop-areacodes-e2c2b2e"
STAC_SEARCH = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
PC_SIGN = "https://planetarycomputer.microsoft.com/api/sas/v1/sign"
LULC_DIR = ROOT / "data" / "raw" / "lulc" / "io-lulc-annual-v02"
WORLDPOP_SERVICE = (
    "https://worldpop.arcgis.com/arcgis/rest/services/"
    "WorldPop_Total_Population_100m/ImageServer"
)
WORLDPOP_PATH = (
    ROOT / "data" / "raw" / "population" / "worldpop" / "worldpop_2020_yongchun.tif"
)
OSM_PATH = ROOT / "data" / "processed" / "osm_features.gpkg"
DEFAULT_OVERPASS = "https://overpass-api.de/api/interpreter"
USER_AGENT = "silishell-data-fetch/1.0 (+https://github.com/Hualalala741/silishell)"


def client(timeout: float = 180) -> httpx.Client:
    return httpx.Client(
        follow_redirects=True,
        timeout=httpx.Timeout(timeout, connect=30),
        headers={"User-Agent": USER_AGENT},
    )


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def download_file(http: httpx.Client, url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    with http.stream("GET", url) as response:
        response.raise_for_status()
        with temporary.open("wb") as stream:
            for chunk in response.iter_bytes(1024 * 1024):
                stream.write(chunk)
    temporary.replace(path)
    print(f"已下载 {path.relative_to(ROOT)}")


def fetch_boundaries(force: bool = False) -> None:
    if BOUNDARY_PATH.exists() and BOUNDARY_METADATA_PATH.exists() and not force:
        print("跳过 geoBoundaries：文件已存在（使用 --force 覆盖）")
        return
    with client() as http:
        response = http.get(BOUNDARY_API)
        response.raise_for_status()
        metadata = response.json()
        write_json(BOUNDARY_METADATA_PATH, metadata)
        download_file(http, metadata["gjDownloadURL"], BOUNDARY_PATH)


def fetch_admin_codes(force: bool = False) -> None:
    files = {"result.csv": "result.csv", "LICENSE": "LICENSE", "README.md": "README.md"}
    with client() as http:
        for target_name, source_name in files.items():
            target = ADMIN_DIR / target_name
            if target.exists() and not force:
                print(f"跳过 {target.relative_to(ROOT)}：文件已存在")
                continue
            download_file(http, f"{ADMIN_BASE}/{source_name}", target)
    (ADMIN_DIR / "SOURCE.md").write_text(
        "# Source\n\n"
        f"- Repository: https://github.com/yescallop/areacodes\n"
        f"- Commit: `{ADMIN_COMMIT}`\n"
        "- License: CC0 1.0 (see `LICENSE`)\n",
        encoding="utf-8",
    )


def yongchun_geometry(boundary_path: Path = BOUNDARY_PATH):
    if not boundary_path.exists():
        raise FileNotFoundError("缺少县界文件；请先运行 boundaries 子命令")
    boundaries = gpd.read_file(boundary_path)
    names = boundaries["shapeName"].fillna("").astype(str).str.casefold()
    selected = boundaries.loc[names == "yongchunxian"]
    if selected.empty:
        raise RuntimeError("在 geoBoundaries CHN ADM2 中找不到 Yongchunxian")
    geometry = selected.to_crs("EPSG:4326").geometry.union_all()
    if geometry.is_empty:
        raise RuntimeError("永春县边界为空")
    return geometry


def signed_asset(http: httpx.Client, href: str) -> str:
    response = http.get(PC_SIGN, params={"href": href})
    response.raise_for_status()
    return str(response.json()["href"])


def fetch_lulc(years: list[int], force: bool = False) -> None:
    geometry = yongchun_geometry()
    with client(300) as http:
        for year in years:
            output = LULC_DIR / f"io_lulc_{year}_yongchun.tif"
            if output.exists() and not force:
                print(f"跳过 {output.relative_to(ROOT)}：文件已存在")
                continue
            response = http.post(
                STAC_SEARCH,
                json={
                    "collections": ["io-lulc-annual-v02"],
                    "intersects": geometry.__geo_interface__,
                    "datetime": f"{year}-01-01/{year}-12-31",
                    "limit": 100,
                },
            )
            response.raise_for_status()
            items = [
                item
                for item in response.json().get("features", [])
                if str(item.get("id", "")).endswith(f"-{year}")
            ]
            if not items:
                raise RuntimeError(f"Planetary Computer 未返回 {year} 年土地覆盖")
            output.parent.mkdir(parents=True, exist_ok=True)
            with ExitStack() as stack:
                datasets = []
                memories = []
                profile = None
                for item in items:
                    href = signed_asset(http, item["assets"]["data"]["href"])
                    source = stack.enter_context(rasterio.open(href))
                    source_geometry = transform_geom("EPSG:4326", source.crs, geometry.__geo_interface__)
                    data, transform = mask(source, [source_geometry], crop=True, nodata=0)
                    profile = source.profile.copy()
                    profile.update(
                        driver="GTiff",
                        height=data.shape[1],
                        width=data.shape[2],
                        transform=transform,
                        nodata=0,
                    )
                    memory = MemoryFile()
                    memories.append(memory)
                    dataset = memory.open(**profile)
                    dataset.write(data)
                    datasets.append(dataset)
                mosaic, transform = merge(datasets, nodata=0)
                assert profile is not None
                profile.update(
                    height=mosaic.shape[1],
                    width=mosaic.shape[2],
                    transform=transform,
                    compress="deflate",
                )
                with rasterio.open(output, "w", **profile) as target:
                    target.write(mosaic)
                for dataset in datasets:
                    dataset.close()
                for memory in memories:
                    memory.close()
            write_json(
                output.with_suffix(".source.json"),
                {
                    "source": "Microsoft Planetary Computer",
                    "collection": "io-lulc-annual-v02",
                    "year": year,
                    "item_ids": [item["id"] for item in items],
                    "license": "CC BY 4.0",
                    "processing": "masked to the geoBoundaries Yongchunxian geometry",
                },
            )
            print(f"已生成 {output.relative_to(ROOT)}")


def fetch_worldpop(force: bool = False) -> None:
    if WORLDPOP_PATH.exists() and not force:
        print(f"跳过 {WORLDPOP_PATH.relative_to(ROOT)}：文件已存在")
        return
    bounds = yongchun_geometry().bounds
    WORLDPOP_PATH.parent.mkdir(parents=True, exist_ok=True)
    resolution = 0.0008333323579634333
    width = math.ceil((bounds[2] - bounds[0]) / resolution)
    height = math.ceil((bounds[3] - bounds[1]) / resolution)
    if width > 4100 or height > 4100:
        raise RuntimeError("WorldPop 导出范围超过 ImageServer 的 4100 像素限制")
    with client(300) as http:
        response = http.get(
            f"{WORLDPOP_SERVICE}/exportImage",
            params={
                "bbox": ",".join(str(value) for value in bounds),
                "bboxSR": 4326,
                "imageSR": 4326,
                "size": f"{width},{height}",
                "format": "tiff",
                "pixelType": "F32",
                "time": 1577836800000,
                "f": "json",
            },
        )
        response.raise_for_status()
        payload = response.json()
        if "href" not in payload:
            raise RuntimeError(f"WorldPop ImageServer 导出失败：{payload}")
        download_file(http, payload["href"], WORLDPOP_PATH)
    with rasterio.open(WORLDPOP_PATH) as source:
        if source.count != 1 or source.crs is None:
            raise RuntimeError("WorldPop 导出文件不是有效的单波段地理栅格")
    write_json(
        WORLDPOP_PATH.with_suffix(".source.json"),
        {
            "source": "WorldPop / Esri ImageServer",
            "source_doi": "10.5258/SOTON/WP00645",
            "service": WORLDPOP_SERVICE,
            "year": 2020,
            "license": "CC BY 4.0",
            "requested_bounds": list(bounds),
            "export_extent": payload.get("extent"),
        },
    )
    print(f"已生成 {WORLDPOP_PATH.relative_to(ROOT)}")


OSM_LAYERS: dict[str, tuple[str, str]] = {
    "major_roads": (
        'way(area.a)["highway"~"^(motorway|trunk|primary|secondary|tertiary)(_link)?$"];',
        "line",
    ),
    "highway_exits": ('node(area.a)["highway"="motorway_junction"];', "point"),
    "rivers": ('way(area.a)["waterway"~"^(river|stream|canal)$"];', "line"),
    "settlements": ('nwr(area.a)["place"~"^(city|town|village)$"];', "point"),
    "town_centers": ('nwr(area.a)["place"="town"]["name"];', "point"),
}


def osm_geometry(element: dict[str, Any], geometry_type: str):
    if element.get("type") == "node" and "lon" in element:
        geometry = Point(float(element["lon"]), float(element["lat"]))
    elif "center" in element:
        geometry = Point(float(element["center"]["lon"]), float(element["center"]["lat"]))
    else:
        coordinates = [
            (float(node["lon"]), float(node["lat"]))
            for node in element.get("geometry", [])
            if "lon" in node and "lat" in node
        ]
        if len(coordinates) < 2:
            return None
        if geometry_type == "line":
            geometry = LineString(coordinates)
        else:
            geometry = (
                Polygon(coordinates) if len(coordinates) >= 4 and coordinates[0] == coordinates[-1]
                else LineString(coordinates)
            ).centroid
    return geometry


def overpass_layer(
    http: httpx.Client,
    endpoint: str,
    area_id: int,
    selector: str,
    geometry_type: str,
) -> gpd.GeoDataFrame:
    query = f"[out:json][timeout:180];area({area_id})->.a;{selector}out tags center geom;"
    response = http.post(endpoint, data={"data": query})
    response.raise_for_status()
    rows = []
    seen = set()
    for element in response.json().get("elements", []):
        osm_id = f"{element.get('type')}/{element.get('id')}"
        if osm_id in seen:
            continue
        geometry = osm_geometry(element, geometry_type)
        if geometry is None:
            continue
        seen.add(osm_id)
        tags = element.get("tags") or {}
        rows.append(
            {
                "osm_id": osm_id,
                "name": tags.get("name") or tags.get("name:zh") or "",
                "category": tags.get("highway")
                or tags.get("waterway")
                or tags.get("place")
                or tags.get("amenity")
                or tags.get("industrial")
                or tags.get("building")
                or tags.get("shop")
                or tags.get("craft")
                or tags.get("office")
                or "",
                "geometry": geometry,
            }
        )
    if not rows:
        return gpd.GeoDataFrame(
            {"osm_id": [], "name": [], "category": []},
            geometry=gpd.GeoSeries([], crs="EPSG:4326"),
            crs="EPSG:4326",
        )
    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")


def fetch_osm(
    endpoint: str = DEFAULT_OVERPASS,
    area_id: int = 3602666967,
    delay: float = 2,
    force: bool = False,
) -> None:
    OSM_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = OSM_PATH.with_name(f"{OSM_PATH.stem}.part.gpkg")
    progress_path = temporary.with_suffix(".progress.json")
    if OSM_PATH.exists() and not force and not temporary.exists():
        print(f"跳过 {OSM_PATH.relative_to(ROOT)}：文件已存在")
        return
    if force and temporary.exists():
        temporary.unlink()
    if force and progress_path.exists():
        progress_path.unlink()
    completed = set()
    if temporary.exists():
        completed.update(gpd.list_layers(temporary)["name"].tolist())
    if progress_path.exists():
        completed.update(json.loads(progress_path.read_text(encoding="utf-8")))
    written = []
    endpoints = list(
        dict.fromkeys([endpoint, "https://overpass.kumi.systems/api/interpreter"])
    )
    with client(240) as http:
        for index, (name, (selector, geometry_type)) in enumerate(OSM_LAYERS.items()):
            if name in completed:
                print(f"跳过 OSM 图层 {name}：已在未完成产物中")
                continue
            if index:
                time.sleep(delay)
            layer = None
            last_error: Exception | None = None
            for current_endpoint in endpoints:
                for attempt in range(1, 4):
                    try:
                        layer = overpass_layer(
                            http, current_endpoint, area_id, selector, geometry_type
                        )
                        break
                    except (httpx.HTTPError, json.JSONDecodeError) as exc:
                        last_error = exc
                        if attempt < 3:
                            time.sleep(2**attempt)
                if layer is not None:
                    break
            if layer is None:
                raise RuntimeError(f"OSM 图层 {name} 获取失败") from last_error
            if layer.empty:
                print(f"警告：OSM 图层 {name} 为空，未写入 GeoPackage")
            else:
                layer.to_file(temporary, layer=name, driver="GPKG")
                written.append({"layer": name, "feature_count": len(layer)})
                print(f"{name}: {len(layer)}")
            completed.add(name)
            write_json(progress_path, sorted(completed))
    if not temporary.exists():
        raise RuntimeError("Overpass 未返回任何可用要素")
    layer_counts = []
    for layer_name in gpd.list_layers(temporary)["name"]:
        layer_counts.append(
            {"layer": layer_name, "feature_count": len(gpd.read_file(temporary, layer=layer_name))}
        )
    temporary.replace(OSM_PATH)
    progress_path.unlink(missing_ok=True)
    write_json(
        OSM_PATH.with_suffix(".source.json"),
        {
            "source": "OpenStreetMap contributors",
            "license": "ODbL 1.0",
            "endpoints": endpoints,
            "overpass_area_id": area_id,
            "layers": layer_counts,
        },
    )
    print(f"已生成 {OSM_PATH.relative_to(ROOT)}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="获取并处理资源选址使用的公开数据")
    parser.add_argument(
        "dataset", choices=["boundaries", "admin-codes", "lulc", "worldpop", "osm", "all"]
    )
    parser.add_argument("--force", action="store_true", help="覆盖已有产物")
    parser.add_argument("--years", type=int, nargs="+", default=[2017, 2022, 2023])
    parser.add_argument("--overpass-endpoint", default=DEFAULT_OVERPASS)
    parser.add_argument("--osm-area-id", type=int, default=3602666967)
    parser.add_argument("--delay", type=float, default=2)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.dataset in {"boundaries", "all"}:
        fetch_boundaries(args.force)
    if args.dataset in {"admin-codes", "all"}:
        fetch_admin_codes(args.force)
    if args.dataset in {"lulc", "all"}:
        fetch_lulc(args.years, args.force)
    if args.dataset in {"worldpop", "all"}:
        fetch_worldpop(args.force)
    if args.dataset in {"osm", "all"}:
        fetch_osm(args.overpass_endpoint, args.osm_area_id, args.delay, args.force)


if __name__ == "__main__":
    main()
