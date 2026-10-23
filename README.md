# 政府政策数据平台

这是一个面向永春县政策监测和资源选址的 Web 项目：Next.js 提供操作界面，FastAPI 负责政策检索、网页抓取、定时任务和选址计算，PostgreSQL/PostGIS 保存全部业务数据。

## 快速启动

需要 Docker Desktop，以及一个已经导入本项目表结构和业务数据的 Neon PostgreSQL 数据库。首次使用先创建本地环境配置：

```bash
cp .env.example .env
```

在 `.env` 中填写 Neon 提供的池化 `DATABASE_URL`、DashScope 和高德地图配置，然后启动：

```bash
docker compose up --build
```

启动后访问：

- Web：`http://127.0.0.1:3000`
- API 文档：`http://127.0.0.1:8000/docs`
- API 健康检查：`http://127.0.0.1:8000/health`

停止服务使用 `docker compose down`。Compose 只运行 Web 和 API；业务数据保存在 Neon，不会因为删除或重建容器而丢失。

`DATABASE_URL` 必须使用 Neon 的池化连接，主机名通常包含 `-pooler`。用于 `pg_dump`、`pg_restore` 或迁移的直连地址可另填为 `DATABASE_URL_UNPOOLED`，应用运行时不使用它。不要把真实连接地址提交到 GitHub。

## 本地开发

需要 Python 3.11–3.13、`uv` 和 Node.js：

```bash
uv sync --dev
CRAWL4_AI_BASE_DIRECTORY=.runtime \
PLAYWRIGHT_BROWSERS_PATH=.runtime/ms-playwright \
crawl4ai-setup
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

## 主题配置

政策搜索关键词不是 TXT 文件，而是保存在 `config/topics/` 下的 JSON 主题配置中。Web 工作台启动后，
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

`urls.txt` 与主题配置用途不同：它只是一组已知政策详情页 URL，供旧的批量抓取命令逐行读取；
Web 工作台的“搜索并发现政策”功能不读取该文件。

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

```bash
uv run python scripts/build_site_candidates.py abandoned_cropland \
  --historical data/raw/landcover-2017.tif \
  --recent data/raw/landcover-2022.tif data/raw/landcover-2023.tif

uv run python scripts/import_rental_communities.py \
  --rent-listings output/rent/yongchun-rent.csv \
  --geocodes output/rent/yongchun-community-geocodes.csv

uv run python scripts/compute_site_selection_metrics.py abandoned_cropland \
  --features data/processed/osm_features.gpkg \
  --population data/interim/worldpop_2020_yongchun.tif \
  --county-context data/processed/county_context.json
```

其他维护脚本：

- `crawl_yongchun_rent.py`：低频采集公开租房挂牌。
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

## 部署

推荐保持单仓库：

1. Vercel 从 `apps/web` 构建 Next.js。
2. Cloud Run Service 从 `Dockerfile.api` 构建 FastAPI。
3. Neon 或其他支持 PostGIS 的托管 PostgreSQL 保存业务数据。
4. 前端配置 `NEXT_PUBLIC_API_URL`，服务端配置 `INTERNAL_API_URL`。
5. API 配置 `DATABASE_URL`、LLM 密钥和正式的 `CORS_ALLOW_ORIGINS`。

不要提交 `.env`。`NEXT_PUBLIC_` 开头的变量会进入浏览器代码，不能存放数据库密码或 LLM 密钥。Cloud Run 可能缩容或启动多个实例，生产定时任务建议迁移到 Cloud Run Job/Cloud Scheduler，不能依赖 API 进程永久常驻。
