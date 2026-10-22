from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import zipfile
from pathlib import Path
from typing import Any, Iterator
from xml.etree import ElementTree as ET

import psycopg
from dotenv import load_dotenv
from psycopg.types.json import Jsonb


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "sql" / "001_data_warehouse_layers.sql"
CELL_REF = re.compile(r"([A-Z]+)")
MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def column_number(cell_reference: str) -> int:
    match = CELL_REF.match(cell_reference)
    if not match:
        raise ValueError(f"无效的 Excel 单元格坐标: {cell_reference}")
    result = 0
    for char in match.group(1):
        result = result * 26 + ord(char) - ord("A") + 1
    return result


def shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        raw = archive.open("xl/sharedStrings.xml")
    except KeyError:
        return []
    values: list[str] = []
    with raw:
        for _, element in ET.iterparse(raw, events=("end",)):
            if element.tag == f"{{{MAIN_NS}}}si":
                values.append("".join(node.text or "" for node in element.iter(f"{{{MAIN_NS}}}t")))
                element.clear()
    return values


def workbook_sheets(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {
        rel.attrib["Id"]: rel.attrib["Target"]
        for rel in relationships.findall(f"{{{PACKAGE_REL_NS}}}Relationship")
    }
    result: list[tuple[str, str]] = []
    for sheet in workbook.findall(f".//{{{MAIN_NS}}}sheet"):
        relationship_id = sheet.attrib[f"{{{REL_NS}}}id"]
        target = targets[relationship_id].lstrip("/")
        if not target.startswith("xl/"):
            target = f"xl/{target}"
        result.append((sheet.attrib["name"], target))
    return result


def parse_numeric(raw: str) -> int | float | str:
    try:
        if not any(marker in raw.lower() for marker in (".", "e")):
            return int(raw)
        return float(raw)
    except ValueError:
        return raw


def cell_value(cell: ET.Element, strings: list[str]) -> Any:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.iter(f"{{{MAIN_NS}}}t"))
    value = cell.find(f"{{{MAIN_NS}}}v")
    if value is None or value.text is None:
        return None
    raw = value.text
    if cell_type == "s":
        return strings[int(raw)]
    if cell_type in {"str", "e"}:
        return raw
    if cell_type == "b":
        return raw == "1"
    return parse_numeric(raw)


def iter_sheet_rows(
    archive: zipfile.ZipFile, target: str, strings: list[str]
) -> Iterator[tuple[int, list[Any]]]:
    with archive.open(target) as stream:
        for _, element in ET.iterparse(stream, events=("end",)):
            if element.tag != f"{{{MAIN_NS}}}row":
                continue
            row_number = int(element.attrib["r"])
            values: list[Any] = []
            for cell in element.findall(f"{{{MAIN_NS}}}c"):
                index = column_number(cell.attrib["r"])
                while len(values) < index:
                    values.append(None)
                values[index - 1] = cell_value(cell, strings)
            yield row_number, values
            element.clear()


def register_source_file(
    connection: psycopg.Connection[Any],
    *,
    path: Path,
    dataset_key: str,
    source_type: str,
    content_type: str,
    source_url: str | None = None,
    represented_year: int | None = None,
    license_name: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> int:
    checksum = sha256(path)
    try:
        storage_path = path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError(f"原始文件必须位于项目目录内: {path}") from exc
    row = connection.execute(
        """
        INSERT INTO ods.source_files (
            dataset_key, source_type, original_filename, content_type,
            source_url, represented_year, license_name, sha256,
            byte_size, metadata, storage_path
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (sha256) DO UPDATE SET
            dataset_key = EXCLUDED.dataset_key,
            source_type = EXCLUDED.source_type,
            original_filename = EXCLUDED.original_filename,
            content_type = EXCLUDED.content_type,
            source_url = EXCLUDED.source_url,
            represented_year = EXCLUDED.represented_year,
            license_name = EXCLUDED.license_name,
            byte_size = EXCLUDED.byte_size,
            metadata = EXCLUDED.metadata,
            storage_path = EXCLUDED.storage_path
        RETURNING id
        """,
        (
            dataset_key,
            source_type,
            path.name,
            content_type,
            source_url,
            represented_year,
            license_name,
            checksum,
            path.stat().st_size,
            Jsonb(metadata or {}),
            storage_path,
        ),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"无法登记源文件: {path}")
    return int(row[0])


def import_excel(
    connection: psycopg.Connection[Any], path: Path, source_file_id: int
) -> dict[str, int]:
    existing = connection.execute(
        """
        SELECT sheet_name, count(*)
        FROM ods.county_climate_economy
        WHERE source_file_id = %s
        GROUP BY sheet_name
        ORDER BY sheet_name
        """,
        (source_file_id,),
    ).fetchall()
    if existing:
        return {str(sheet_name): int(count) for sheet_name, count in existing}
    connection.execute(
        "DELETE FROM ods.county_climate_economy WHERE source_file_id = %s",
        (source_file_id,),
    )
    counts: dict[str, int] = {}
    with zipfile.ZipFile(path) as archive:
        strings = shared_strings(archive)
        for sheet_name, target in workbook_sheets(archive):
            count = 0
            with connection.cursor().copy(
                """
                COPY ods.county_climate_economy
                    (source_file_id, sheet_name, source_row_number, row_data)
                FROM STDIN
                """
            ) as copy:
                for row_number, values in iter_sheet_rows(archive, target, strings):
                    copy.write_row((source_file_id, sheet_name, row_number, Jsonb(values)))
                    count += 1
            counts[sheet_name] = count
    return counts


def import_boundaries(
    connection: psycopg.Connection[Any], path: Path, source_file_id: int
) -> int:
    existing = connection.execute(
        "SELECT count(*) FROM ods.boundary_features WHERE source_file_id = %s",
        (source_file_id,),
    ).fetchone()
    if existing and int(existing[0]) > 0:
        return int(existing[0])
    payload = json.loads(path.read_text(encoding="utf-8"))
    features = payload.get("features")
    if payload.get("type") != "FeatureCollection" or not isinstance(features, list):
        raise ValueError("围栏文件不是有效的 GeoJSON FeatureCollection")
    connection.execute(
        "DELETE FROM ods.boundary_features WHERE source_file_id = %s", (source_file_id,)
    )
    rows = []
    for index, feature in enumerate(features, start=1):
        properties = feature.get("properties") or {}
        geometry = feature.get("geometry")
        if not geometry:
            raise ValueError(f"第 {index} 个围栏缺少 geometry")
        rows.append(
            (
                source_file_id,
                index,
                properties.get("shapeID"),
                properties.get("shapeName"),
                Jsonb(properties),
                json.dumps(geometry, ensure_ascii=False, separators=(",", ":")),
            )
        )
    with connection.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO ods.boundary_features (
                source_file_id, source_feature_number, source_feature_id,
                source_feature_name, source_properties, geom
            ) VALUES (
                %s, %s, %s, %s, %s,
                ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)
            )
            """,
            rows,
        )
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="导入县域气候 Excel 和 geoBoundaries 到 ODS")
    parser.add_argument(
        "--excel", type=Path, default=ROOT / "2000-2024数据1.xlsx"
    )
    parser.add_argument(
        "--boundary",
        type=Path,
        default=ROOT / "data" / "ods-source" / "geoBoundaries-CHN-ADM2.geojson",
    )
    parser.add_argument(
        "--boundary-metadata",
        type=Path,
        default=ROOT / "data" / "ods-source" / "geoboundaries-CHN-ADM2-metadata.json",
    )
    parser.add_argument(
        "--osm-pbf",
        type=Path,
        default=ROOT / "data" / "raw" / "osm" / "fujian-20260913.osm.pbf",
    )
    parser.add_argument(
        "--admin-codes",
        type=Path,
        default=ROOT
        / "data"
        / "raw"
        / "admin-codes"
        / "yescallop-areacodes-e2c2b2e"
        / "result.csv",
    )
    parser.add_argument(
        "--lulc-dir",
        type=Path,
        default=ROOT / "data" / "raw" / "lulc" / "io-lulc-annual-v02",
    )
    parser.add_argument(
        "--worldpop",
        type=Path,
        default=ROOT
        / "data"
        / "raw"
        / "population"
        / "worldpop"
        / "worldpop_2020_yongchun.tif",
    )
    args = parser.parse_args()

    lulc_files = {
        year: args.lulc_dir / f"io_lulc_{year}_yongchun.tif"
        for year in (2017, 2022, 2023)
    }

    load_dotenv(ROOT / ".env")
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("未配置 DATABASE_URL")
    for path in (
        args.excel,
        args.boundary,
        args.boundary_metadata,
        args.osm_pbf,
        args.admin_codes,
        *lulc_files.values(),
        args.worldpop,
    ):
        if not path.is_file():
            raise FileNotFoundError(path)

    boundary_metadata = json.loads(args.boundary_metadata.read_text(encoding="utf-8"))
    with psycopg.connect(database_url) as connection:
        connection.execute(MIGRATION.read_text(encoding="utf-8"))
        connection.commit()
        with connection.transaction():
            excel_id = register_source_file(
                connection,
                path=args.excel,
                dataset_key="county_climate_2000_2024",
                source_type="excel_workbook",
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                metadata={"description": "2000-2024 县域气候与社会经济面板原始工作簿"},
            )
            metadata_id = register_source_file(
                connection,
                path=args.boundary_metadata,
                dataset_key="china_county_boundaries_2017",
                source_type="geoboundaries_metadata",
                content_type="application/json",
                source_url="https://www.geoboundaries.org/api/current/gbOpen/CHN/ADM2/",
                represented_year=2017,
                license_name=boundary_metadata.get("boundaryLicense"),
                metadata=boundary_metadata,
            )
            boundary_id = register_source_file(
                connection,
                path=args.boundary,
                dataset_key="china_county_boundaries_2017",
                source_type="geojson_boundary",
                content_type="application/geo+json",
                source_url=boundary_metadata.get("gjDownloadURL"),
                represented_year=2017,
                license_name=boundary_metadata.get("boundaryLicense"),
                metadata={
                    "metadata_source_file_id": metadata_id,
                    "boundary_id": boundary_metadata.get("boundaryID"),
                    "boundary_type": boundary_metadata.get("boundaryType"),
                    "boundary_canonical": boundary_metadata.get("boundaryCanonical"),
                    "attribution": "geoBoundaries",
                },
            )
            register_source_file(
                connection,
                path=args.osm_pbf,
                dataset_key="fujian_osm",
                source_type="osm_pbf",
                content_type="application/vnd.openstreetmap.data+pbf",
                represented_year=2026,
                license_name="Open Data Commons Open Database License (ODbL) 1.0",
                metadata={
                    "coverage": "福建省",
                    "original_source_filename": "fujian-latest.osm.pbf",
                    "osm_snapshot_at": "2026-09-13T01:11:53Z",
                    "yongchun_relation_id": 2666967,
                    "verified_yongchun_highway_way_count": 2103,
                },
            )
            register_source_file(
                connection,
                path=args.admin_codes,
                dataset_key="china_admin_codes_1981_2025",
                source_type="csv_admin_code_history",
                content_type="text/csv",
                source_url=(
                    "https://raw.githubusercontent.com/yescallop/areacodes/"
                    "e2c2b2e1fb4ce1fb76e879aa0b80e229adaf950c/result.csv"
                ),
                license_name="CC0 1.0",
                metadata={
                    "coverage_year_start": 1981,
                    "coverage_year_end": 2025,
                    "levels": ["省级", "地级", "县级"],
                    "row_count": 6824,
                    "upstream_repository": "https://github.com/yescallop/areacodes",
                    "upstream_commit": "e2c2b2e1fb4ce1fb76e879aa0b80e229adaf950c",
                },
            )
            for year, path in lulc_files.items():
                register_source_file(
                    connection,
                    path=path,
                    dataset_key="io_lulc_annual_v02_yongchun",
                    source_type="geotiff_land_cover",
                    content_type="image/tiff",
                    source_url=(
                        "https://planetarycomputer.microsoft.com/api/stac/v1/"
                        "collections/io-lulc-annual-v02"
                    ),
                    represented_year=year,
                    license_name="Creative Commons Attribution 4.0 (CC BY 4.0)",
                    metadata={
                        "coverage": "永春县",
                        "stac_collection": "io-lulc-annual-v02",
                        "source_item_id": "not recorded by original acquisition script",
                        "acquired_at_local": "2026-09-15",
                        "processing": "clipped to Yongchun boundary with rasterio.mask",
                        "crs": "EPSG:32650",
                        "resolution_m": 10,
                        "width": 8508,
                        "height": 3767,
                        "bounds": [569520.0, 2789300.0, 654600.0, 2826970.0],
                        "band_count": 1,
                        "dtype": "uint8",
                        "nodata": 0,
                        "class_codes": {
                            "1": "water",
                            "2": "trees",
                            "4": "flooded vegetation",
                            "5": "crops",
                            "7": "built area",
                            "8": "bare ground",
                            "9": "snow or ice",
                            "10": "clouds",
                            "11": "rangeland",
                        },
                    },
                )
            register_source_file(
                connection,
                path=args.worldpop,
                dataset_key="worldpop_china_population_2020_yongchun",
                source_type="geotiff_population",
                content_type="image/tiff",
                source_url="https://hub.worldpop.org/doi/10.5258/SOTON/WP00055",
                represented_year=2020,
                license_name="Creative Commons Attribution 4.0 (CC BY 4.0)",
                metadata={
                    "coverage": "永春县及外接矩形范围",
                    "source_product": "WorldPop China 100m Population",
                    "source_variant": "not recorded by original acquisition process",
                    "retrieval_method": "ArcGIS ImageServer clip/export",
                    "retrieval_endpoint": "not retained",
                    "acquired_at_local": "2026-09-15",
                    "crs": "EPSG:4326",
                    "resolution_degrees": [
                        0.000833170731707318,
                        0.0008331707317073163,
                    ],
                    "width": 1014,
                    "height": 410,
                    "bounds": [
                        117.6912139390244,
                        25.214951,
                        118.53604906097561,
                        25.556551,
                    ],
                    "band_count": 1,
                    "dtype": "float32",
                    "units": "estimated persons per grid square",
                    "nodata_declared": False,
                    "observed_fill_value": 3.3999999521443642e38,
                    "quality_note": (
                        "GeoTIFF does not declare NoData; values near 3.4e38 must be "
                        "treated as missing during DWD processing"
                    ),
                },
            )
            sheet_counts = import_excel(connection, args.excel, excel_id)
            boundary_count = import_boundaries(connection, args.boundary, boundary_id)
            counts = {
                "source_files": 9,
                "county_climate_economy": sheet_counts,
                "boundary_features": boundary_count,
            }
        print(json.dumps(counts, ensure_ascii=False))


if __name__ == "__main__":
    main()
