# 政府政策数据平台

这个项目完成一条流水线：政策 URL → Crawl4AI 清洗网页 → LLM 按指定字段抽取 → PostgreSQL → APScheduler 定时重复。

另外包含一个不调用 LLM 的永春县主题监测流程：搜索接口发现页面 → 配置词过滤 → PostgreSQL 去重 → Crawl4AI 只抓待抓取详情页 → APScheduler 定时执行。

## 1. 安装

需要 Python 3.11–3.13。推荐用 `uv` 创建独立环境：

```bash
uv sync
source .venv/bin/activate
CRAWL4_AI_BASE_DIRECTORY=.runtime \
PLAYWRIGHT_BROWSERS_PATH=.runtime/ms-playwright \
crawl4ai-setup
cp .env.example .env
```

没有 `uv` 时也可以用 Python 3.11–3.13 的 `venv` 加 `pip install -e .`。

编辑 `.env`，填入模型名称和密钥：

```dotenv
LLM_PROVIDER=dashscope/qwen3.5-plus-2026-04-20
DASHSCOPE_API_KEY=你的百炼_API_Key
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
TIMEZONE=Asia/Shanghai
DATABASE_URL=postgresql://data_agent:你的本地密码@127.0.0.1:5432/data_agent
```

Crawl4AI 通过 LiteLLM 支持多种模型。使用其他服务时，把 `LLM_PROVIDER` 和密钥改成相应值即可；本地 Ollama 通常不需要密钥。

### Docker 一键运行（推荐用于交接）

仓库保留源代码和 Docker 构建说明，不提交 `.env`、虚拟环境、浏览器缓存、数据库文件或抓取输出。
首次使用先创建本地配置：

```bash
cp .env.example .env
```

填写 `.env` 中的 LLM、高德和本地 PostgreSQL 密码后运行：

```bash
docker compose up --build
```

启动后访问：

- Web：`http://127.0.0.1:3000`
- API 健康检查：`http://127.0.0.1:8000/health`
- PostgreSQL：宿主机 `127.0.0.1:5433`，容器之间使用 `postgres:5432`

第一次构建需要下载 Python 依赖和 Chromium，耗时和镜像体积都会明显高于普通 Web 项目。停止服务使用
`docker compose down`；数据库保存在 Docker volume 中。只有明确不再需要本地数据库时才执行
`docker compose down --volumes`。

API 与爬虫共用 `Dockerfile.api`：默认命令启动 FastAPI，也可以使用同一镜像执行一次性命令，例如：

```bash
docker compose run --rm api policy-crawler monitor --topic zhuchanye --years 2
```

Python 测试与前端检查：

```bash
uv sync --dev
uv run pytest -q
cd apps/web && npm ci && npm run typecheck && npm run build
```

## 2. 立即抓取一次

```bash
policy-crawler run \
  --url "https://某政府网站/政策原文.html" \
  --fields "标题,发文机关,文号,发布日期,生效日期,适用地区,摘要" \
  --requirements "只采用正文明确表述；摘要重点写政策对象、措施和申报条件" \
  --format both
```

结果默认写入 PostgreSQL 的 `ods.policy_extractions`。需要文件副本时加
`--format json`、`--format csv` 或 `--format both`；CSV 使用 UTF-8 BOM 编码，可以直接用 Excel 打开。

## 3. 批量抓取详情页

新建 `urls.txt`，每行放一个政策详情页 URL。空行和以 `#` 开头的注释会被忽略，重复 URL 会自动去重：

```text
# 国务院政策
https://www.example.gov.cn/policy/001.html
https://www.example.gov.cn/policy/002.html
```

然后执行：

```bash
policy-crawler batch \
  --url-file urls.txt \
  --concurrency 2 \
  --fields "标题,发文机关,文号,发布日期,生效日期,适用地区,摘要" \
  --requirements "只采用正文明确表述；摘要重点写政策对象、措施和申报条件" \
  --format both
```

同一批 URL 共用一个浏览器，默认同时处理 2 个页面，并在请求之间加入随机等待。结果写入 `ods.policy_extractions`；每条记录包含 `status` 和 `error`，单页失败不会中断整批任务。需要汇总 JSON/CSV 时显式传 `--format both`。

## 4. 定时执行

每天北京时间 08:00 执行：

```bash
policy-crawler schedule \
  --url-file urls.txt \
  --concurrency 2 \
  --fields "标题,发文机关,文号,发布日期,摘要" \
  --cron "0 8 * * *"
```

每 30 分钟执行：

```bash
policy-crawler schedule \
  --url-file urls.txt \
  --fields "标题,发文机关,文号,发布日期,摘要" \
  --interval-minutes 30
```

调度器必须保持运行；关闭终端或进程后任务也会停止。正式部署可用 systemd、Docker 或云平台守护这个进程。

## 政府政策抓取时的实际边界

- 优先抓“政策原文详情页”，不要把政策列表页当作详情页抽取。主题监测流程已经把列表发现和详情抓取拆成两个阶段。
- 程序启用了 `robots.txt` 检查。还应遵守目标网站的使用条款、访问频率和公开数据范围，给同一站点留出合理间隔。
- 扫描版 PDF 没有文本层时，网页抓取拿不到正文，需要另接 OCR。
- 验收时抽样对照原文，重点检查文号、日期、适用地区和“有效/废止”状态；LLM 抽取不能替代来源核验。
- 同一政府站点页面结构稳定后，可以为高频字段改用 CSS/XPath 抽取，只让 LLM 生成摘要，以降低费用并提高稳定性。

## Crawl4AI 和 LLM 各自负责什么

- Crawl4AI/Chromium：请求网页、执行 JavaScript、遵守 robots.txt、清理导航和页脚、生成 Markdown 正文。
- LLM：读取清理后的 Markdown，按 `--fields` 和 `--requirements` 转成结构化字段。
- Python：校验 URL、控制批量并发、处理失败记录、写入 JSON/CSV。

所以网络抓取不由 LLM 完成；当前每个成功抓取的详情页会调用一次或多次 LLM，长页面分块时调用次数可能增加。

## 永春县主题监测（不调用 LLM）

主题配置独立放在 `config/topics/`：

```text
config/topics/
├── zhuchanye.json
└── soy_products.json
```

每个 JSON 包含主题名、搜索词、主体词、上下文词、排除词和最低相关分。程序要求结果同时命中主体词和上下文词，再根据标题命中、来源类型和多搜索词交叉命中计算相关分。

运行竹产业主题：

```bash
policy-crawler monitor --topic zhuchanye --years 2
```

只抓取 2 条豆制品详情页作为小批量示例：

```bash
policy-crawler monitor --topic soy_products --years 1 --limit 2
```

执行过程：

1. 程序直接 POST 永春县公开搜索接口，按配置中的 `queries` 分页取得候选结果。
2. 根据主题配置过滤并计算相关分。
3. 把全部合格候选写入 PostgreSQL 的 `ods.policy_documents`，以“主题 + 详情页 URL”去重。
4. Crawl4AI 抓取还未成功抓取的详情页，不使用 LLM。`--limit` 只限制当轮数量，其余候选会在后续运行时继续抓取。
5. HTML、Markdown 和抓取状态保存在 `ods.policy_documents`；运行报告保存在 `ops.crawl_runs`。

已成功抓取的 URL 默认不会重复抓取。需要重新抓取时，使用 `--refresh-existing`。

每天北京时间 08:00 执行豆制品监测：

```bash
policy-crawler monitor-schedule \
  --topic soy_products \
  --years 2 \
  --cron "0 8 * * *"
```

新增主题时，复制一份 JSON 并修改词表，然后把文件名传给 `--topic` 即可，不需要改 Python 代码。调度进程必须保持运行；生产环境可用 systemd 或 Docker 保证进程重启。

## 竹产业政策画像

对已抓取目录执行 section 相关性召回和原子 measure 抽取：

```bash
policy-crawler portrait --topic zhuchanye
```

默认从 PostgreSQL 读取已抓取正文。兼容旧数据时仍可传
`--pages-dir "data/pages/旧目录"`。画像文件作为人工查看和复算用的导出件保留在
`output/portrait-*/`；数据库在 `ops.crawl_runs` 记录运行及输出目录，并将measure、评分和画像分别写入DWD、DWS和ADS。

流程使用 `text-embedding-v4`（默认 1024 维）筛选与竹产业相关的 section，再使用
`qwen3.5-plus-2026-04-20` 按项目内置的结构化字段抽取 measure。模型可通过
`EMBEDDING_MODEL`、`EMBEDDING_DIMENSIONS` 和 `LLM_PROVIDER` 覆盖。

每次运行会在 `output/portrait-*/` 下生成：

- `documents.json`：正文与 manifest 的匹配情况；
- `sections.json` 和 `selected_sections.json`：切分及召回结果；
- `raw_extractions.json`：Qwen 返回的原始 JSON，便于不重复调用模型地调整校验规则；
- `measures.json`：经逐字证据校验后的原子政策陈述；
- `policy_scores.json`：measure 去重、分项分数及县域等权诊断分；
- `portrait.json` 和 `portrait.md`：结构化画像及可读报告；
- `warnings.json`：来源重映射和抽取校验告警。

画像中的力度、明确性、去重和聚合分数由结构化字段确定性计算，不需要再次调用 LLM。

## 永春县租房挂牌采集

`scripts/crawl_yongchun_rent.py` 低频读取安居客公开列表页，提取月租、面积、
元/平方米/月、户型、小区和地址。脚本先检查 `robots.txt`，翻页默认等待 8–12 秒；
遇到验证码或访问频率限制会立即停止，不会尝试绕过。

先抓一页验证：

```bash
.venv/bin/python scripts/crawl_yongchun_rent.py --pages 1
```

按当前页面显示的页数抓取时，保持默认低频即可，例如：

```bash
.venv/bin/python scripts/crawl_yongchun_rent.py --pages 6
```

结果保存在 `output/rent/` 的 JSON 和 UTF-8 BOM CSV 文件中。挂牌租金适合作为
居住空间成本代理值，不应直接解释为农业用地的元/亩/年租金。

如果只修改了本地校验、状态判断或聚合逻辑，可以复用一次已有的模型结果：

```bash
policy-crawler portrait \
  --pages-dir data/pages/竹产业_20261006_204026_381 \
  --topic zhuchanye \
  --reuse-run output/portrait-竹产业_20261006_204026_381-YYYYMMDD-HHMMSS
```

## 5. Web 工作台

Web 工作台采用 Next.js + FastAPI，把主题监测拆成两个需要用户确认的阶段：

1. 载入主题词表，编辑检索词、主题词、场景词、排除词、最低相关分和搜索日期范围。
2. 确认词表后检索候选页面，在独立滚动窗口中查看相关分并勾选结果。
3. 再次确认后才抓取正文。候选、Markdown、原始 HTML、状态和运行记录全部写入 PostgreSQL。

先启动 API：

```bash
uv sync
uv run policy-crawler-api
```

再启动前端：

```bash
cd apps/web
npm install
npm run dev
```

浏览器访问 `http://127.0.0.1:3000`。前端默认连接 `http://127.0.0.1:8000`；需要修改时设置 `NEXT_PUBLIC_API_URL`。

### 资源选址数据链路

资源选址页不再读取 `public/site-selection-snapshot.json`。页面将面积和权重提交到 FastAPI 的
`POST /site-selection/rank`，硬筛选和四维加权排名均由后端完成；当前行业固定为竹产业，政策项
直接读取最新完成的 `dws.county_policy_scores` 竹产业总分。

- `dwd.site_candidate_lands`：按土地覆盖栅格规则识别并矢量化后的候选斑块；
- `ads.site_candidate_metrics`：道路、高速口、居民点、河流、人口、成本与三项基础分；
- `ads.rental_community_samples`：租赁明细按小区聚合并完成坐标解析后的租金样本。

迁移会创建上述表，但不会预填 ADS 数据。生成候选斑块时，疑似撂荒地使用“历史为耕地且
各近年持续为低利用代理类别”的规则，稳定自然资源使用“所有输入年份均为裸地/草灌地代理
类别”的规则：

```bash
uv run python scripts/build_site_candidates.py abandoned_cropland \
  --historical data/raw/landcover-2017.tif \
  --recent data/raw/landcover-2022.tif data/raw/landcover-2023.tif

uv run python scripts/build_site_candidates.py stable_natural_resource \
  --annual data/raw/landcover-2017.tif data/raw/landcover-2022.tif data/raw/landcover-2023.tif
```

候选斑块写入 DWD 后，再显式运行指标任务发布 ADS 数据：

```bash
uv run python scripts/import_rental_communities.py \
  --rent-listings output/rent/yongchun-rent-20261012-004458.csv \
  --geocodes output/rent/yongchun-community-geocodes.csv

uv run python scripts/compute_site_selection_metrics.py abandoned_cropland \
  --features data/processed/osm_features.gpkg \
  --population data/interim/worldpop_2020_yongchun.tif \
  --county-context data/processed/county_context.json
```

租金项使用候选地中心 5 km 内小区的平均单位租金；邻域无样本时保留缺失标记，并以中性 50 分
参与成本合成。风险暂无数据，统一取中性 50 分。市场和交通沿用 OSM + WorldPop 口径。任务会
替换相应候选类型已有的 ADS 指标；未运行时页面正常显示候选总数，但不生成缺少指标的推荐结果。

## PostgreSQL 数据结构与旧数据迁移

项目固定使用 `DATABASE_URL` 指向的 PostgreSQL。业务数据按 ODS、DWD、DWS、ADS 分层，采集运行状态单独放在 OPS：

- `ods.policy_documents`：候选元数据、抓取状态和完整 Markdown/HTML 正文；
- `ods.policy_extractions`：通用政策抽取任务的原始结果；
- `dwd.policy_measures`：原子政策陈述；
- `dws.county_policy_scores`：县域政策工具评分；
- `ads.county_policy_portraits`：面向应用的县域政策画像；
- `dwd.site_candidate_lands`：资源选址候选斑块；
- `ads.site_candidate_metrics`、`ads.rental_community_samples`：选址指标与小区租金应用数据；
- `ops.crawl_runs`、`ops.project_profiles`、`ops.crawl_schedules`：运行、配置和调度。

把旧 SQLite、`data/` 和 `output/` 中现有资料迁移进库：

```bash
policy-crawler migrate-postgres
```

迁移命令可重复执行；主题文档按“主题 + URL”更新，画像输出文件保留在原路径，数据库只保存运行记录和分层后的业务数据。

常用核对 SQL：

```sql
SELECT crawl_status, count(*)
FROM ods.policy_documents
GROUP BY crawl_status;

SELECT id, run_type, requirement_name, status, started_at
FROM ops.crawl_runs
ORDER BY id DESC
LIMIT 20;
```

## 县域气候与社会经济数据仓库

项目中的县域气候、社会经济、行政区围栏和道路数据采用
ODS、DWD、DWS、ADS 四层结构管理。原始文件保存在项目 `data` 目录，ODS
只保存文件路径、哈希、大小和来源元数据，不保存文件二进制本体。县域气候与社会经济数据
目前只加载到 ODS；源工作簿完成校验前不构建对应 DWD、DWS 和 ADS 业务表。

福建省 OpenStreetMap 快照保存在
`data/raw/osm/fujian-20260913.osm.pbf`，包含截至 2026-09-13 09:11:53
（北京时间）的 OSM 数据；该时间是数据截止时间，不是下载时间。永春县道路应从该 PBF 按行政区关系
`2666967` 裁剪，不再通过 `ods.osm_elements` 保存 Overpass API 响应。

数据库 Schema 由 `sql/` 下的 PostgreSQL 建表脚本定义。

## 6. GitHub 与托管部署

这是一个单仓库项目，不需要为前端、API 和爬虫复制三份代码。推荐流程是：

1. 把源代码、`Dockerfile.api`、`Dockerfile.web`、`compose.yaml` 和 `.env.example` 提交到 GitHub；
2. Vercel 从 `apps/web` 构建 Next.js，并在构建环境设置 `NEXT_PUBLIC_API_URL` 和高德公开配置；
3. Cloud Run Service 从 `Dockerfile.api` 构建 FastAPI，设置 `DATABASE_URL`、LLM 密钥和正式的 `CORS_ALLOW_ORIGINS`；
4. Neon 托管 PostgreSQL/PostGIS，生产环境不使用 Compose 中的本地数据库；
5. 需要自动抓取时，再用同一个 API 镜像创建 Cloud Run Job，并由 Cloud Scheduler 触发。

业务数据不提交到 GitHub。把现有 PostgreSQL 迁到托管数据库时，应通过 `pg_dump`/`pg_restore`
或托管平台提供的迁移工具单独传输；数据库备份文件已被 `.gitignore` 排除。

不要向 GitHub 提交 `.env`。`NEXT_PUBLIC_` 开头的变量会进入浏览器代码，其中不能放数据库密码或
LLM 密钥。当前 API 内置 APScheduler 适合本地单实例使用；Cloud Run 会缩容或启动多个实例，生产定时
任务应迁移到 Cloud Run Job/Cloud Scheduler，不能依赖 API 进程常驻。

`/policyNet` 和资源选址页均通过 FastAPI 从 PostgreSQL 读取业务数据，不依赖部署机器上的
`data/` 或 `output/` 目录。
