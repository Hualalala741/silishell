#!/usr/bin/env python3
"""Build the county-level cost and climate context with source URLs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


NASA_POWER_URL = (
    "https://power.larc.nasa.gov/api/temporal/daily/point?"
    "parameters=PRECTOTCORR,T2M_MAX,T2M_MIN&community=AG&longitude=118.12&latitude=25.37&"
    "start=20210101&end=20251231&format=JSON"
)
MINIMUM_WAGE_URL = (
    "https://rst.fujian.gov.cn/zw/zfxxgk/zfxxgkml/zyywgz/jycj/202501/"
    "t20250127_6710184.htm"
)
CENSUS_URL = "https://fjyc.gov.cn/zwgk/tjxx/202106/t20210603_2568641.htm"
WATER_URL = "https://www.fjyc.gov.cn/zwgk/tzgg/202508/t20250818_3201140.htm"
ELECTRICITY_URL = (
    "https://www.fjyx.gov.cn/zwgk/jgsf/jgsfzc/202210/P020221018607530154795.pdf"
)
LAND_COMPENSATION_URL = (
    "https://www.fjyc.gov.cn/zwgk/zrzy/zdcq/202402/t20240220_3006311.htm"
)
DISASTER_2023_URL = (
    "https://www.fjyc.gov.cn/zwgk/zfxxgkzl/xzzf/yudou/ml/202404/"
    "P020240726797809628778.pdf"
)
DISASTER_2024_URL = (
    "https://www.fjyc.gov.cn/zwgk/czzj/bmyjs/bmjs/202511/"
    "P020251126385170740306.pdf"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成县级人工、能源和气候风险数据")
    parser.add_argument(
        "--nasa-power",
        type=Path,
        default=Path("data/raw/nasa_power_yongchun_2021_2025.json"),
    )
    parser.add_argument(
        "--output", type=Path, default=Path("data/processed/county_context.json")
    )
    return parser.parse_args()


def climate_summary(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    parameters = payload["properties"]["parameter"]
    annual = []
    for year in range(2021, 2026):
        dates = [date for date in parameters["PRECTOTCORR"] if date.startswith(str(year))]
        if not dates:
            raise ValueError(f"NASA POWER 数据缺少 {year} 年记录")
        annual.append(
            {
                "year": year,
                "heavy_rain_days_ge_50mm": sum(
                    parameters["PRECTOTCORR"][date] >= 50 for date in dates
                ),
                "extreme_rain_days_ge_100mm": sum(
                    parameters["PRECTOTCORR"][date] >= 100 for date in dates
                ),
                "hot_days_ge_35c": sum(parameters["T2M_MAX"][date] >= 35 for date in dates),
                "cold_days_le_0c": sum(parameters["T2M_MIN"][date] <= 0 for date in dates),
                "maximum_daily_rain_mm": max(parameters["PRECTOTCORR"][date] for date in dates),
                "maximum_temperature_c": max(parameters["T2M_MAX"][date] for date in dates),
                "minimum_temperature_c": min(parameters["T2M_MIN"][date] for date in dates),
            }
        )
    avg_heavy = sum(row["heavy_rain_days_ge_50mm"] for row in annual) / len(annual)
    avg_hot = sum(row["hot_days_ge_35c"] for row in annual) / len(annual)
    avg_cold = sum(row["cold_days_le_0c"] for row in annual) / len(annual)
    gridded_hazard = (
        0.5 * min(avg_heavy / 10, 1)
        + 0.25 * min(avg_hot / 30, 1)
        + 0.25 * min(avg_cold / 30, 1)
    )
    official_events = [
        {
            "year": 2023,
            "typhoon_count": 2,
            "heavy_rain_processes": 7,
            "convective_processes": 12,
            "source_url": DISASTER_2023_URL,
        },
        {
            "year": 2024,
            "typhoon_count": 2,
            "heavy_rain_processes": 7,
            "convective_processes": 14,
            "source_url": DISASTER_2024_URL,
        },
    ]
    avg_typhoon = sum(row["typhoon_count"] for row in official_events) / len(official_events)
    avg_process = sum(row["heavy_rain_processes"] for row in official_events) / len(
        official_events
    )
    avg_convective = sum(row["convective_processes"] for row in official_events) / len(
        official_events
    )
    event_hazard = (
        0.4 * min(avg_typhoon / 5, 1)
        + 0.35 * min(avg_process / 15, 1)
        + 0.25 * min(avg_convective / 25, 1)
    )
    safety = (1 - (gridded_hazard * 0.6 + event_hazard * 0.4)) * 100
    return {
        "normalized_safety_score": round(safety, 1),
        "score_range": "0-100，越高代表气候风险越低",
        "formula": "60%逐日阈值风险 + 40%政府报告灾害过程风险；各计数按预设上限归一化",
        "daily_thresholds": {
            "heavy_rain_mm": 50,
            "extreme_rain_mm": 100,
            "hot_c": 35,
            "cold_c": 0,
        },
        "annual_daily_metrics": annual,
        "official_event_metrics": official_events,
        "source_url": NASA_POWER_URL,
        "limitation": "NASA POWER为较粗网格再分析数据，当前按县级背景使用，不作为地块气候适宜性结论",
    }


def build_context(nasa_power: Path) -> dict:
    return {
        "labor": {
            "minimum_wage_yuan_month": 2045,
            "part_time_minimum_wage_yuan_hour": 21.5,
            "effective_date": "2025-04-01",
            "provincial_minimum_wage_tiers": [1895, 2045, 2195, 2265],
            "minimum_wage_source_url": MINIMUM_WAGE_URL,
            "average_education_years_age_15_plus": 8.88,
            "university_per_100k": 7578,
            "high_school_per_100k": 10361,
            "education_source_url": CENSUS_URL,
            "spatial_scope": "永春县；所有候选地使用同一县级值",
        },
        "utilities": {
            "nonresidential_water_yuan_ton": 2.35,
            "water_breakdown": {"base": 1.70, "raw_water_fee": 0.55, "water_resource_tax": 0.10},
            "water_effective_date": "2025-09-01",
            "water_source_url": WATER_URL,
            "agricultural_electricity_low_voltage_yuan_kwh": 0.575,
            "agricultural_irrigation_low_voltage_yuan_kwh": 0.2477,
            "electricity_source_url": ELECTRICITY_URL,
            "spatial_scope": "县/省级目录价；不区分县内地块",
        },
        "land_cost_fallback": {
            "expropriation_compensation_yuan_hectare": 570000,
            "expropriation_compensation_yuan_mu": 38000,
            "source_url": LAND_COMPENSATION_URL,
            "usage": "仅作县级土地价值旁证，不与年租金直接混算",
        },
        "climate_risk": climate_summary(nasa_power),
    }


def main() -> None:
    args = parse_args()
    context = build_context(args.nasa_power)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(context, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"已写入县级成本与气候风险数据：{args.output}")


if __name__ == "__main__":
    main()
