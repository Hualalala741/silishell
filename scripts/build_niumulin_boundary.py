from __future__ import annotations

import argparse
import json
import re
from html.parser import HTMLParser
from pathlib import Path

from pyproj import Transformer
from shapely.geometry import Polygon
from shapely.ops import transform


SOURCE_URL = (
    "https://sthjt.fujian.gov.cn/zwgk/zfxxgkzl/zfxxgkml/mlstbh/"
    "201809/t20180904_4469888.htm"
)
COORDINATE_PATTERN = re.compile(
    r"(\d+)°(\d+)'(\d+(?:\.\d+)?)\"\s*E\s*[,，]\s*"
    r"(\d+)°(\d+)'(\d+(?:\.\d+)?)\"\s*N"
)
SECTIONS = (
    ("一、自然保护区范围四至边界", "reserve_outer_boundary", 264.84),
    ("二、缓冲区外围四至边界", "buffer_outer_boundary", 84.17 + 17.89),
    ("三、核心区外围四至边界", "core_outer_boundary", 84.17),
)


class ParagraphParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.depth = 0
        self.parts: list[str] = []
        self.paragraphs: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() == "p":
            if self.depth == 0:
                self.parts = []
            self.depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "p" and self.depth:
            self.depth -= 1
            if self.depth == 0:
                self.paragraphs.append(" ".join("".join(self.parts).split()))

    def handle_data(self, data: str) -> None:
        if self.depth:
            self.parts.append(data)


def dms_to_decimal(parts: tuple[str, ...]) -> tuple[float, float]:
    lon_d, lon_m, lon_s, lat_d, lat_m, lat_s = parts
    longitude = int(lon_d) + int(lon_m) / 60 + float(lon_s) / 3600
    latitude = int(lat_d) + int(lat_m) / 60 + float(lat_s) / 3600
    return round(longitude, 9), round(latitude, 9)


def find_section_coordinates(paragraphs: list[str], heading: str) -> list[list[float]]:
    index = paragraphs.index(heading)
    coordinates = [
        list(dms_to_decimal(match.groups()))
        for match in COORDINATE_PATTERN.finditer(paragraphs[index + 1])
    ]
    if len(coordinates) < 4:
        raise ValueError(f"{heading} 未提取到足够的坐标")
    coordinates.append(coordinates[0])
    return coordinates


def area_hectares(coordinates: list[list[float]]) -> float:
    # 官方坐标为 CGCS2000；永春县使用 UTM 50N 计算近似平面面积。
    project = Transformer.from_crs("EPSG:4490", "EPSG:32650", always_xy=True).transform
    return transform(project, Polygon(coordinates)).area / 10_000


def build_geojson(source_html: Path) -> dict:
    parser = ParagraphParser()
    parser.feed(source_html.read_text(encoding="utf-8"))
    features = []
    for heading, boundary_role, declared_area in SECTIONS:
        coordinates = find_section_coordinates(parser.paragraphs, heading)
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "name": "福建永春牛姆林省级自然保护区",
                    "boundary_role": boundary_role,
                    "declared_area_ha": round(declared_area, 2),
                    "derived_straight_line_area_ha": round(area_hectares(coordinates), 2),
                    "coordinate_reference_system": "CGCS2000 (EPSG:4490)",
                    "source_document": "闽环然函〔2018〕37号",
                    "source_url": SOURCE_URL,
                    "source_date": "2018-08-29",
                    "accuracy_note": (
                        "由官方正文列出的拐点坐标按直线连接生成；山脊、沟谷、道路等"
                        "自然边界未加密刻画，仅适合地图展示和初筛，不替代勘界矢量或审批结论。"
                    ),
                },
                "geometry": {"type": "Polygon", "coordinates": [coordinates]},
            }
        )
    return {
        "type": "FeatureCollection",
        "name": "yongchun_niumulin_protected_area_text_coordinates",
        "features": features,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="从福建省生态环境厅公开页面整理牛姆林保护区文字坐标。"
    )
    parser.add_argument("--source-html", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = build_geojson(args.source_html)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for feature in payload["features"]:
        properties = feature["properties"]
        print(
            properties["boundary_role"],
            f"declared={properties['declared_area_ha']:.2f} ha",
            f"derived={properties['derived_straight_line_area_ha']:.2f} ha",
        )


if __name__ == "__main__":
    main()
