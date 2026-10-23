# 政府政策数据平台

这是一个面向永春县政策监测和资源选址的 Web 项目：Next.js 提供操作界面，FastAPI 负责政策检索、网页抓取、定时任务和选址计算，PostgreSQL/PostGIS 保存全部业务数据。

## 快速启动

需要 Docker Desktop，以及一个已经导入本项目表结构和业务数据的 Neon PostgreSQL 数据库。首次使用先创建本地环境配置：

```bash
cp .env.example .env
```

在 `.env` 中至少填写 Neon 提供的池化 `DATABASE_URL`。DashScope 只在重新生成政策画像时需要，高德 Key 只用于资源选址页的卫星底图；不使用这两项功能时可以留空。
首次启动，或代码、依赖、Dockerfile 有更新时：
```bash
docker compose up --build
```
之后启动运行：
```bash
docker compose up
```

启动后访问：

- Web：`http://127.0.0.1:3000`
- API 文档：`http://127.0.0.1:8000/docs`
- API 健康检查：`http://127.0.0.1:8000/health`

停止服务使用 `docker compose down`。Compose 只运行 Web 和 API；业务数据保存在 Neon。


## 环境变量

`.env.example` 是可提交的模板，`.env` 才是本地实际配置。下表列出模板中每个变量的用途和必需程度：

| 变量 | 何时需要 | 对应功能与说明 |
| --- | --- | --- |
| `DATABASE_URL` | **必填** | FastAPI 和数据维护脚本使用的 PostgreSQL/PostGIS 池化连接。Docker 启动时也会检查它。 |
| `DATABASE_URL_UNPOOLED` | 备份/迁移时可选 | Neon 直连地址，供 `pg_dump`、`pg_restore` 和 schema 迁移使用；当前运行中的 Web/API 不读取它。 |
| `NEON_BRANCH` | 可选 | 用于标记希望使用的 Neon 分支名；当前应用和脚本不读取该变量，真正连接哪个分支由 `DATABASE_URL` 决定。 |
| `LLM_PROVIDER` | 重建政策画像时 | `build_policy_portrait.py` 使用的 LLM 模型名。 |
| `DASHSCOPE_API_KEY` | 重建政策画像时 | 阿里云百炼 DashScope 访问密钥，同时用于 Embedding 和 LLM 请求。脚本也接受同类的 `LLM_API_KEY`，但模板默认使用前者。 |
| `LLM_BASE_URL` | 重建政策画像时 | OpenAI 兼容的 LLM/Embedding API 地址，默认为 DashScope 兼容端点。 |
| `NEXT_PUBLIC_AMAP_KEY` | 使用选址地图时 | 高德 Web 端 JS API Key，用于资源选址页的卫星底图。 |
| `NEXT_PUBLIC_AMAP_SECURITY_JS_CODE` | 使用选址地图时 | 与高德 Key 配套的安全密钥；2021 年 12 月之后申请的 Key 通常需要填写。 |
| `AMAP_WEB_SERVICE_KEY` | 重新生成租房地理编码时 | 高德 Web 服务端 Key，只由 `geocode_rental_communities.py` 在本地使用；不要复用或暴露浏览器 JS API Key。 |
| `NEXT_PUBLIC_API_URL` | 按部署地址调整 | 浏览器访问 FastAPI 的地址，在 Next.js 构建时注入。Docker 本地开发默认为 `http://127.0.0.1:8000`。 |
| `INTERNAL_API_URL` | 服务端分开部署时 | Next.js 服务端访问 FastAPI 的地址，政策图谱页首屏取数使用。Compose 内部自动使用 `http://api:8000`。 |
| `CORS_ALLOW_ORIGINS` | 前端域名变更时 | 允许调用 API 的浏览器 Origin，多个地址用逗号分隔。当前 `compose.yaml` 对本地容器固定为 `localhost/127.0.0.1:3000`，正式部署需改成真实前端域名。 |
| `HOST` | 通常保留默认 | FastAPI 监听地址。本地运行默认 `127.0.0.1`，Compose 内自动覆盖为 `0.0.0.0`。 |
| `PORT` | 端口冲突时 | FastAPI 监听端口，默认 `8000`。Compose 目前固定使用 `8000`。 |
| `TIMEZONE` | 通常保留默认 | 政策画像产出时间的时区，默认 `Asia/Shanghai`。抓取和计划任务代码当前也固定为上海时区。 |
| `CRAWL4_AI_BASE_DIRECTORY` | 通常保留默认 | Crawl4AI 的运行缓存和数据目录。Compose 内自动改为 `/app/.runtime` 并挂载命名卷。 |
| `PLAYWRIGHT_BROWSERS_PATH` | 通常保留默认 | Playwright 浏览器二进制的安装/查找目录。Compose 内自动改为 `/opt/ms-playwright`。 |

`NEXT_PUBLIC_` 开头的变量会进入浏览器代码，不能在其中存放数据库密码、LLM 密钥或其他服务端秘密。

## 本地开发

需要 Python 3.11–3.13、`uv` 和 Node.js：

```bash
uv sync --dev
CRAWL4_AI_BASE_DIRECTORY=.runtime \
PLAYWRIGHT_BROWSERS_PATH=.runtime/ms-playwright \
uv run crawl4ai-setup
cp .env.example .env
```

启动 API：

```bash
uv run policy-crawler-api
```

另开终端启动前端：

```bash
cd apps/web
npm ci
npm run dev
```

## Web 工作流

政策监测在 Web 工作台完成，不需要运行旧命令行抓取命令：

1. 载入并编辑主题词、检索词、排除词、最低相关分和日期范围。
2. FastAPI 调用永春县公开搜索接口并返回候选政策页面。
3. 用户确认候选页面后，Crawl4AI 抓取正文。
4. 候选元数据、Markdown、HTML 和运行状态写入 PostgreSQL。
5. 定时任务在 Web 的计划任务页面创建和管理。

主要 API：

- `GET /topics`：读取主题配置。
- `POST /search`：搜索并过滤候选政策。
- `POST /crawl`：抓取用户确认的政策正文。
- `GET/PUT/DELETE /schedules`：管理定时监测。
- `GET /projects`：列出已抓取项目。
- `GET /policy-network`：读取政策画像网络。
- `POST /site-selection/rank`：执行资源选址筛选和排名。

程序会检查 `robots.txt`，不尝试绕过验证码或访问限制。扫描版 PDF 没有文本层时需要另接 OCR。

## 数据来源与处理链路

### 政策图谱

政策原文来自[永春县人民政府官方网站](https://www.fjyc.gov.cn/)。`policy_crawler/topic_monitor.py` 向站内公开搜索接口 `https://www.fjyc.gov.cn/ssp/search/api/search` 提交主题词，用户在 Web 页确认候选结果后，同一模块通过 Crawl4AI 抓取正文并写入 PostgreSQL。`scripts/build_policy_portrait.py` 再从已抓取正文中拆分原子政策措施、计算政策工具分数，并生成图谱页读取的 DWD/DWS/ADS 数据。

截至 **2026-10-23** 对当前数据库 `zhuchanye` 主题的实际查看，政策图谱最新一次运行名为“永春县竹产业政策画像”，包含 **9 篇来源文档和 74 条原子政策措施**；文档发布时间范围为 2025-11-14 至 2026-08-14，来源域名均为 `www.fjyc.gov.cn`，发布方包括永春县人民政府、永春县人民政府办公室和永春县林业局。这是可随抓取和画像重建变化的数据库快照，不是固定写死在前端的数据。

### 资源选址与地理数据

| 数据 | 原始来源 | 在项目中的用途 | 对应脚本 |
| --- | --- | --- | --- |
| 道路、高速出口、河流、聚落和乡镇 | [OpenStreetMap](https://www.openstreetmap.org/copyright) / Overpass，ODbL 1.0 | `data/processed/osm_features.gpkg` 中的五个核心图层用于计算交通、水系和聚落距离 | `scripts/fetch_public_data.py osm` 获取并生成 GeoPackage；`compute_site_selection_metrics.py` 读取；`import_ods_sources.py` 登记来源 |
| 2017、2022、2023 年土地覆盖 | 微软 Planetary Computer 上的 [Impact Observatory / Esri 10 米年度土地利用覆盖 V2](https://planetarycomputer.microsoft.com/api/stac/v1/collections/io-lulc-annual-v02)，源影像为 Sentinel-2，CC BY 4.0 | 裁剪到永春县后，以历史耕地以及近期裸地/草灌地等类别识别疑似擂荒地和稳定自然资源斑块 | `scripts/fetch_public_data.py lulc` 搜索 STAC、签名资产并裁剪；`build_site_candidates.py` 生成候选斑块 |
| 2020 年人口栅格 | [WorldPop Total Population 100m ImageServer](https://worldpop.arcgis.com/arcgis/rest/services/WorldPop_Total_Population_100m/ImageServer)，原数据 DOI `10.5258/SOTON/WP00645`，WGS84，约 100 米分辨率，CC BY 4.0 | 计算每个候选斑块中心 5 公里范围人口 | `scripts/fetch_public_data.py worldpop` 按永春县范围和 2020 年时间切片调用 `exportImage`；`compute_site_selection_metrics.py` 计算指标 |
| 中国县级行政区边界 | [geoBoundaries CHN ADM2](https://www.geoboundaries.org/api/current/gbOpen/CHN/ADM2/)，代表年份 2017，PDDL 1.0 | 县域定位、边界存档和裁剪参考 | `scripts/fetch_public_data.py boundaries` 下载；`import_ods_sources.py` 导入 GeoJSON 及元数据 |
| 行政区划代码历史 | [`yescallop/areacodes`](https://github.com/yescallop/areacodes) 的 `result.csv`，当前锁定 commit `e2c2b2e1fb4ce1fb76e879aa0b80e229adaf950c`，CC0 1.0 | 1981–2025 年省/地/县三级行政代码溯源 | `scripts/fetch_public_data.py admin-codes` 按固定 commit 下载；`import_ods_sources.py` 登记源 CSV |
| 牛姆林保护区范围 | [福建省生态环境厅《闽环然函〔2018〕37 号》](https://sthjt.fujian.gov.cn/zwgk/zfxxgkzl/zfxxgkml/mlstbh/201809/t20180904_4469888.htm) 正文中的拐点坐标 | 按公开坐标直线连接生成近似 GeoJSON，仅用于地图展示和初筛，不代替正式勘界或审批结论 | `scripts/build_niumulin_boundary.py`，需通过 `--source-html` 提供保存的官方页面 |
| 公开租房挂牌（部分） | [安居客永春租房列表](https://yongc.zu.anjuke.com/) | 目前只爬取了部分数据；聚合为小区租金样本，用于候选地周边 5 公里居住租金参考；挂牌价不等于成交价 | `crawl_yongchun_rent.py` 采集；`geocode_rental_communities.py` 使用高德 Web 服务 Key 生成坐标 CSV；`import_rental_communities.py` 聚合入库 |
| 县域气候与社会经济面板 | 项目所有者本地持有的 `2000-2024数据1.xlsx`（约 53 MB） | 原始工作簿按 sheet/行保存到 ODS | 不随 Git 仓库发布；获得合法副本后放到 `data/raw/2000-2024数据1.xlsx`，再由 `scripts/import_ods_sources.py` 导入 |
| 工资、水价、电价和县级气候背景 | `data/processed/county_context.json`，其中保留福建省人社厅、永春县政府、电价目录和 NASA POWER 原始链接 | 计算候选地劳动力和公用事业成本指标，并保留县级气候背景 | `fetch_climate_risk.py` 获取逐日气候；`build_county_context.py` 按明示规则重建；`compute_site_selection_metrics.py` 使用 |



## 主题配置

政策搜索关键词保存在 `config/topics/` 下的 JSON 主题配置中。Web 工作台启动后，
`GET /topics` 会读取该目录内的全部 `.json` 文件，并把它们显示为可选择的主题。例如
`config/topics/zhuchanye.json` 对应主题标识 `zhuchanye`：

```json
{
  "name": "竹产业",
  "queries": ["竹产业", "竹子种植", "毛竹", "竹林", "竹材", "竹制品", "竹笋", "以竹代塑"],
  "subject_terms": ["竹子", "毛竹", "竹林", "竹产业", "竹材", "竹制品"],
  "context_terms": ["种植", "加工", "补助", "扶持", "项目", "政策", "申报"],
  "exclude_terms": ["竹荪", "竹柳"],
  "minimum_relevance_score": 45
}
```

- `queries`：逐个提交给永春县站内搜索接口的检索词，用于扩大候选范围。
- `subject_terms`：判断搜索结果是否属于目标产业的主题词。
- `context_terms`：判断结果是否包含政策、项目、扶持、生产经营等相关场景。
- `exclude_terms`：命中后直接排除的无关词。
- `minimum_relevance_score`：候选结果必须达到的最低相关分，范围为 0–100。

新增主题时，复制一个现有 JSON 文件并修改内容，文件名使用稳定的英文或拼音标识，例如
`config/topics/forest_tourism.json`；重启 API 后就会出现在 Web 工作台中。用户可以在页面上临时调整词表，
确认并抓取后，实际采用的主题条件会保存到 PostgreSQL，供该项目后续任务使用。

## 政策画像

政策画像仍是显式的数据维护任务，因为它会调用 Embedding 和 LLM，并产生新的 DWD、DWS、ADS 数据：

```bash
uv run python scripts/build_policy_portrait.py --topic zhuchanye
```

脚本默认从 PostgreSQL 读取已抓取正文，生成原子政策措施、政策工具评分和县域政策画像，并将结果写回 PostgreSQL。`output/portrait-*` 只是本地复核文件，不提交 GitHub，也不被线上页面读取。

## 资源选址数据维护

资源选址页直接读取 PostgreSQL：

- `dwd.site_candidate_lands`：候选斑块和空间几何。
- `ads.site_candidate_metrics`：交通、人口、成本和风险指标。
- `ads.rental_community_samples`：租赁小区聚合样本。
- `dws.county_policy_scores`：竹产业政策评分。

需要重新生成业务数据时，按顺序运行：

先获取可公开重建的空间数据：

```bash
uv run python scripts/fetch_public_data.py all
uv run python scripts/fetch_climate_risk.py
uv run python scripts/build_county_context.py
```

`import_ods_sources.py` 还需要未随仓库发布的 Excel 面板数据。只有在获得有权使用的副本并放到 `data/raw/2000-2024数据1.xlsx` 后，才运行：

```bash
uv run python scripts/import_ods_sources.py
```

该命令保留已有文件；只有显式加上 `--force` 才会覆盖。各数据也可以用 `boundaries`、`admin-codes`、`lulc`、`worldpop` 或 `osm` 子命令分别获取。之后按顺序运行：

```bash
uv run python scripts/build_site_candidates.py abandoned_cropland \
  --historical data/raw/lulc/io-lulc-annual-v02/io_lulc_2017_yongchun.tif \
  --recent data/raw/lulc/io-lulc-annual-v02/io_lulc_2022_yongchun.tif \
           data/raw/lulc/io-lulc-annual-v02/io_lulc_2023_yongchun.tif

uv run python scripts/import_rental_communities.py \
  --rent-listings output/rent/yongchun-rent.csv \
  --geocodes output/rent/yongchun-community-geocodes.csv

uv run python scripts/compute_site_selection_metrics.py abandoned_cropland \
  --features data/processed/osm_features.gpkg \
  --population data/raw/population/worldpop/worldpop_2020_yongchun.tif \
  --county-context data/processed/county_context.json
```

如需从新的租房列表重新生成小区坐标，先在 `.env` 配置 `AMAP_WEB_SERVICE_KEY`，再运行：

```bash
uv run python scripts/geocode_rental_communities.py \
  --rent-listings output/rent/yongchun-rent.csv \
  --output output/rent/yongchun-community-geocodes.csv
```

其他维护脚本：

- `crawl_yongchun_rent.py`：低频采集公开租房挂牌。
- `fetch_public_data.py`：下载、裁剪并记录公开空间数据来源。
- `fetch_climate_risk.py`：从 NASA POWER 获取永春县中心点逐日气候数据。
- `build_county_context.py`：根据公开来源和明示公式重建县级成本/风险输入。
- `geocode_rental_communities.py`：使用高德 Web 服务生成租房小区 GCJ-02 坐标。
- `build_niumulin_boundary.py`：从公开边界说明生成保护区 GeoJSON。
- `import_ods_sources.py`：登记和导入气候、行政区划、OSM 与栅格源数据。

这些脚本不是 Web 服务的启动入口，只在更新对应业务数据时运行。

## PostgreSQL

项目只使用 PostgreSQL/PostGIS。应用通过 `DATABASE_URL` 连接 Neon，表结构由 `sql/` 下的 SQL 文件建立：

- ODS：原始政策正文和来源记录。
- DWD：原子政策措施和选址候选斑块。
- DWS：县域政策评分。
- ADS：政策画像和选址指标。
- OPS：运行记录、项目配置和调度状态。

业务数据和数据库密码不提交 GitHub。项目当前使用 Neon 托管 PostgreSQL；交接时只需通过安全渠道提供数据库连接信息，不能把真实 `.env` 加入仓库。

## 测试

```bash
uv run pytest -q
cd apps/web
npm run typecheck
npm run build
```
