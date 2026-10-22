"use client";

import { useEffect, useState } from "react";
import CandidateMap from "./CandidateMap";
import styles from "./site-selection.module.css";

type ResourceKey = "abandoned_cropland" | "stable_natural_resource";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

type Candidate = {
  land_id: string;
  area_mu: number;
  cost_score: number;
  market_score: number;
  risk_score: number;
  policy_score: number;
  protected_overlap: boolean | string;
  climate_pass: boolean | string;
  soil_pass: boolean | string;
  major_road_km: number;
  highway_exit_km: number;
  settlement_km: number;
  river_km: number;
  population_5km: number;
  residential_rent_yuan_sqm_month: number | null;
  residential_rent_community_count: number;
  residential_rent_sample_count: number;
  residential_rent_nearest_km: number | null;
  residential_rent_source_level: "within_5km" | "missing";
  minimum_wage_yuan_month: number;
  nonresidential_water_yuan_ton: number;
  agricultural_electricity_yuan_kwh: number;
  town_name: string;
  data_completeness: number;
  longitude: number;
  latitude: number;
  geometry: CandidateGeometry;
};

type CandidateGeometry = {
  type: "Polygon" | "MultiPolygon";
  coordinates: number[][][] | number[][][][];
};

type CandidateLand = {
  land_id: string;
  area_mu: number;
  longitude: number;
  latitude: number;
  geometry: CandidateGeometry;
};

type SelectionResponse = {
  resource_key: ResourceKey;
  label: string;
  source: string;
  total_count: number;
  eligible_count: number;
  scored_count: number;
  ranked_count: number;
  policy: {
    status: string;
    score: number;
    score_version: string;
    run_id: number;
    requirement_name: string;
    completed_at: string;
    dimensions: Array<{ policy_tool_name: string; dimension_score_100: number }>;
  };
  candidate_lands: CandidateLand[];
  candidates: RankedCandidate[];
};

type RankedCandidate = Candidate & { totalScore: number; rank: number };
type Weights = { cost: number; market: number; risk: number; policy: number };

const DEFAULT_WEIGHTS: Weights = { cost: 30, market: 30, risk: 25, policy: 15 };
const RENT_LABELS: Record<string, string> = {
  within_5km: "5 km 内小区均价",
  missing: "5 km 内无样本",
};
const PAGE_SIZE = 10;

function Icon({ name }: { name: "filter" | "pin" | "map" | "leaf" | "road" | "people" | "money" | "info" | "expand" }) {
  const paths = {
    filter: <><path d="M4 5h16M7 12h10M10 19h4"/><circle cx="8" cy="5" r="1.5"/><circle cx="15" cy="12" r="1.5"/><circle cx="12" cy="19" r="1.5"/></>,
    pin: <><path d="M20 10c0 5-8 11-8 11S4 15 4 10a8 8 0 1 1 16 0Z"/><circle cx="12" cy="10" r="2.5"/></>,
    map: <><path d="m3 6 6-3 6 3 6-3v15l-6 3-6-3-6 3Z"/><path d="M9 3v15M15 6v15"/></>,
    leaf: <><path d="M20 4C12 4 5 8 5 15c0 3 2 5 5 5 7 0 10-8 10-16Z"/><path d="M4 21c3-6 7-9 12-12"/></>,
    road: <><path d="M8 3 6 21M16 3l2 18M12 5v4M12 13v4"/></>,
    people: <><circle cx="9" cy="8" r="3"/><path d="M3 21v-2a6 6 0 0 1 12 0v2M16 4.5a3 3 0 0 1 0 6M18 14a5 5 0 0 1 3 4.6V21"/></>,
    money: <><circle cx="12" cy="12" r="9"/><path d="M8 9.5c0-1.4 1.6-2.5 4-2.5s4 1.1 4 2.5-1.4 2-4 2.5-4 1.1-4 2.5 1.6 2.5 4 2.5 4-1.1 4-2.5M12 5v14"/></>,
    info: <><circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/></>,
    expand: <><path d="M8 3H3v5M16 3h5v5M8 21H3v-5M16 21h5v-5"/></>,
  };
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}

function Slider({ label, value, onChange }: { label: string; value: number; onChange: (value: number) => void }) {
  return <label className={styles.sliderRow}>
    <span>{label}</span>
    <input type="range" min="0" max="100" value={value} onChange={(event) => onChange(Number(event.target.value))}/>
    <strong>{value}</strong>
  </label>;
}

function scoreReason(candidate: RankedCandidate) {
  const dimensions = [
    ["成本条件", candidate.cost_score],
    ["市场可达性", candidate.market_score],
    ["风险表现", candidate.risk_score],
    ["政策匹配", candidate.policy_score],
  ] as const;
  const strongest = dimensions.reduce((best, item) => item[1] > best[1] ? item : best);
  const weakest = dimensions.reduce((best, item) => item[1] < best[1] ? item : best);
  return `主要优势：${strongest[0]}；相对短板：${weakest[0]}。`;
}

function GeometryThumbnail({ geometry, rank }: { geometry: CandidateGeometry; rank?: number }) {
  const polygons = geometry.type === "MultiPolygon"
    ? geometry.coordinates as number[][][][]
    : [geometry.coordinates as number[][][]];
  const rings = polygons.map((polygon) => polygon[0]).filter((ring) => ring?.length > 2);
  const points = rings.flat();
  const longitudes = points.map(([longitude]) => longitude);
  const latitudes = points.map(([, latitude]) => latitude);
  const minX = Math.min(...longitudes);
  const maxX = Math.max(...longitudes);
  const minY = Math.min(...latitudes);
  const maxY = Math.max(...latitudes);
  const width = Math.max(maxX - minX, 0.000001);
  const height = Math.max(maxY - minY, 0.000001);
  const scale = Math.min(36 / width, 36 / height);
  const offsetX = (48 - width * scale) / 2;
  const offsetY = (48 - height * scale) / 2;
  const path = rings.map((ring) => ring.map(([longitude, latitude], index) => {
    const x = offsetX + (longitude - minX) * scale;
    const y = 48 - (offsetY + (latitude - minY) * scale);
    return `${index === 0 ? "M" : "L"}${x.toFixed(2)} ${y.toFixed(2)}`;
  }).join(" ") + " Z").join(" ");
  return <div className={styles.geometryThumb} aria-label={rank ? `第 ${rank} 名候选斑块形状` : "候选斑块形状"}>
    <svg viewBox="0 0 48 48" aria-hidden="true"><path d={path}/></svg>
    {rank && <b>{rank}</b>}
  </div>;
}

function paginationItems(current: number, total: number): Array<number | string> {
  const pages = [...new Set([1, total, current - 1, current, current + 1])]
    .filter((value) => value >= 1 && value <= total)
    .sort((a, b) => a - b);
  const items: Array<number | string> = [];
  pages.forEach((value, index) => {
    if (index > 0 && value - pages[index - 1] > 1) items.push(`ellipsis-${value}`);
    items.push(value);
  });
  return items;
}

export default function SiteSelection() {
  const [result, setResult] = useState<SelectionResponse | null>(null);
  const [error, setError] = useState("");
  const [resourceKey, setResourceKey] = useState<ResourceKey>("abandoned_cropland");
  const [minimumArea, setMinimumArea] = useState(50);
  const [weights, setWeights] = useState<Weights>(DEFAULT_WEIGHTS);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const selectResourceKey = (value: ResourceKey) => {
    setResourceKey(value);
    setSelectedId(null);
  };

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      fetch(`${API_URL}/site-selection/rank`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ resource_key: resourceKey, minimum_area_mu: minimumArea, weights }),
        signal: controller.signal,
        cache: "no-store",
      })
        .then(async (response) => {
          if (response.ok) return response.json() as Promise<SelectionResponse>;
          const body = await response.json().catch(() => ({}));
          throw new Error(typeof body.detail === "string" ? body.detail : "资源选址数据加载失败");
        })
        .then((payload) => { setResult(payload); setError(""); })
        .catch((reason: Error) => { if (reason.name !== "AbortError") setError(reason.message); });
    }, 180);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [resourceKey, minimumArea, weights]);

  const current = result;
  const policyScore = result?.policy.score ?? 0;
  const policyStatus = result?.policy.status ?? "竹产业政策评分待载入";
  const ranked = result?.candidates ?? [];
  const candidateLands = result?.candidate_lands ?? [];
  const rankById = new Map(ranked.map((candidate) => [candidate.land_id, candidate.rank]));
  const mapCandidates = candidateLands.map((candidate) => ({ ...candidate, rank: rankById.get(candidate.land_id) ?? null }));

  useEffect(() => {
    if (selectedId && !candidateLands.some((candidate) => candidate.land_id === selectedId)) setSelectedId(null);
  }, [candidateLands, selectedId]);

  useEffect(() => { setPage(1); }, [resourceKey, minimumArea, weights]);

  const selected = ranked.find((candidate) => candidate.land_id === selectedId);
  const selectedMapCandidate = mapCandidates.find((candidate) => candidate.land_id === selectedId);
  const resultCount = ranked.length || candidateLands.length;
  const pageCount = Math.max(1, Math.ceil(resultCount / PAGE_SIZE));
  const currentPage = Math.min(page, pageCount);
  const start = (currentPage - 1) * PAGE_SIZE;
  const displayed = ranked.slice(start, start + PAGE_SIZE);
  const displayedUnscored = candidateLands.slice(start, start + PAGE_SIZE);

  if (error) return <main className={styles.loading}><strong>无法打开资源选址页</strong><span>{error}</span></main>;
  if (!current) return <main className={styles.loading}><span className={styles.loader}/><span>正在从数据库载入候选地…</span></main>;

  return <main className={styles.page}>
    <header className={styles.header}>
      <div>
        <p className={styles.eyebrow}>RESOURCE INTELLIGENCE · YONGCHUN</p>
        <h1>永春县资源选址</h1>
        <p className={styles.subtitle}>从县域资源底图中筛选候选地块，并按企业偏好形成可解释的推荐排序。</p>
      </div>
      <div className={styles.snapshotBadge}><span/>数据库实时数据 · {current.total_count} 个候选斑块</div>
    </header>

    <section className={styles.metrics} aria-label="筛选概览">
      <div><span>识别资源斑块</span><strong>{current.total_count}</strong><small>当前资源类型</small></div>
      <div><span>通过硬筛选</span><strong>{current.eligible_count}</strong><small>面积与已知约束</small></div>
      <div><span>连续面积要求</span><strong>≥ {minimumArea}<em>亩</em></strong><small>可动态调整</small></div>
      <div><span>当前投资行业</span><strong className={styles.industryMetric}>竹产业</strong><small>{policyStatus}</small></div>
    </section>

    <div className={styles.workspace}>
      <aside className={styles.filters}>
        <div className={styles.panelTitle}><Icon name="filter"/><div><span>企业需求</span><strong>筛选与排序</strong></div></div>

        <label className={styles.field}>
          <span>候选资源类型</span>
          <select value={resourceKey} onChange={(event) => selectResourceKey(event.target.value as ResourceKey)}>
            <option value="abandoned_cropland">疑似撂荒耕地</option>
            <option value="stable_natural_resource">稳定裸地/草灌地（潜在资源）</option>
          </select>
          <small>{resourceKey === "stable_natural_resource" ? "连续三年稳定地表覆盖斑块" : "遥感识别的疑似撂荒候选地"}</small>
        </label>

        <label className={styles.field}>
          <span>投资行业</span>
          <input value="竹产业" readOnly aria-readonly="true"/>
          <small>当前版本固定使用竹产业评分模型</small>
        </label>

        <div className={styles.policyCard}>
          <div><span>政策得分</span><strong>{policyScore.toFixed(2)}<small>/ 100</small></strong></div>
          <b>{policyStatus}</b>
          <p>{result?.policy.requirement_name} · {result?.policy.score_version}</p>
          <details>
            <summary>查看政策维度得分</summary>
            <ul>{result?.policy.dimensions.map((item) => <li key={item.policy_tool_name}>{item.policy_tool_name}：{item.dimension_score_100.toFixed(1)}</li>)}</ul>
          </details>
        </div>

        <label className={styles.field}>
          <span>所需连续面积</span>
          <div className={styles.areaInput}><input type="number" min="1" max="5000" step="10" value={minimumArea} onChange={(event) => setMinimumArea(Math.max(1, Number(event.target.value) || 1))}/><b>亩</b></div>
        </label>

        <div className={styles.weightHeader}><span>排序偏好</span><button type="button" onClick={() => setWeights(DEFAULT_WEIGHTS)}>恢复默认</button></div>
        <div className={styles.sliders}>
          <Slider label="成本" value={weights.cost} onChange={(cost) => setWeights({ ...weights, cost })}/>
          <Slider label="市场 / 收益" value={weights.market} onChange={(market) => setWeights({ ...weights, market })}/>
          <Slider label="低风险" value={weights.risk} onChange={(risk) => setWeights({ ...weights, risk })}/>
          <Slider label="政策" value={weights.policy} onChange={(policy) => setWeights({ ...weights, policy })}/>
        </div>

        <div className={styles.sourceNote}><Icon name="info"/><p><strong>当前数据</strong>{current.source}<br/>遥感候选地不代表权属、规划用途或审批结论。</p></div>
      </aside>

      <section className={styles.results}>
        <div className={styles.resultsHeader}>
          <div><p className={styles.eyebrow}>RECOMMENDATIONS</p><h2>推荐结果</h2></div>
          <span>共 {current.eligible_count} 个地块符合条件</span>
        </div>
        <div className={styles.resultList}>
          {displayed.length === 0 && displayedUnscored.length > 0 ? displayedUnscored.map((candidate) => <button type="button" className={`${styles.resultCard} ${selectedId === candidate.land_id ? styles.selectedCard : ""}`} key={candidate.land_id} onClick={() => setSelectedId(candidate.land_id)}>
            <GeometryThumbnail geometry={candidate.geometry}/>
            <div className={styles.cardBody}>
              <div className={styles.cardTop}><div><span>候选斑块</span><h3>{candidate.land_id}</h3></div><strong>待算<small>综合分</small></strong></div>
              <div className={styles.areaLine}><b>{candidate.area_mu.toFixed(1)} 亩</b><span>已通过面积与已知约束，等待 ADS 指标。</span></div>
            </div>
            <span className={styles.chevron}>›</span>
          </button>) : displayed.length === 0 ? <div className={styles.empty}><Icon name="leaf"/><strong>没有地块通过当前条件</strong><span>可以尝试降低连续面积要求。</span></div> : displayed.map((candidate) => <button type="button" className={`${styles.resultCard} ${selectedId === candidate.land_id ? styles.selectedCard : ""}`} key={candidate.land_id} onClick={() => setSelectedId(candidate.land_id)}>
            <GeometryThumbnail geometry={candidate.geometry} rank={candidate.rank}/>
            <div className={styles.cardBody}>
              <div className={styles.cardTop}><div><span>{candidate.town_name || "乡镇待补充"}</span><h3>{candidate.land_id}</h3></div><strong>{candidate.totalScore}<small>综合分</small></strong></div>
              <div className={styles.areaLine}><b>{candidate.area_mu.toFixed(1)} 亩</b><span>{scoreReason(candidate)}</span></div>
              <div className={styles.scoreBar}><i style={{ width: `${candidate.totalScore}%` }}/></div>
              <div className={styles.scoreGrid}>
                <span>成本 <b>{candidate.cost_score.toFixed(0)}</b></span><span>市场 <b>{candidate.market_score.toFixed(0)}</b></span><span>风险 <b>{candidate.risk_score.toFixed(0)}</b></span><span>政策 <b>{candidate.policy_score.toFixed(1)}</b></span>
              </div>
              <div className={styles.cardMeta}><span><Icon name="road"/>主干道 {candidate.major_road_km.toFixed(2)} km</span><span><Icon name="people"/>5 km 人口约 {Math.round(candidate.population_5km).toLocaleString()}</span><span><Icon name="money"/>{candidate.residential_rent_yuan_sqm_month === null ? "5 km 内无租金样本" : `${candidate.residential_rent_yuan_sqm_month.toFixed(2)} 元/㎡·月（${candidate.residential_rent_community_count} 小区）`}</span></div>
            </div>
            <span className={styles.chevron}>›</span>
          </button>)}
        </div>
        {pageCount > 1 && <nav className={styles.pagination} aria-label="推荐结果分页">
          <button type="button" disabled={currentPage === 1} onClick={() => setPage(currentPage - 1)}>上一页</button>
          {paginationItems(currentPage, pageCount).map((item) => typeof item === "number"
            ? <button type="button" className={item === currentPage ? styles.activePage : ""} aria-current={item === currentPage ? "page" : undefined} key={item} onClick={() => setPage(item)}>{item}</button>
            : <span key={item} aria-hidden="true">…</span>)}
          <button type="button" disabled={currentPage === pageCount} onClick={() => setPage(currentPage + 1)}>下一页</button>
        </nav>}
      </section>

      <section className={styles.mapPanel}>
        <div className={styles.mapHeader}>
          <div><p className={styles.eyebrow}>SPATIAL VIEW</p><h2>{selected ? `${selected.town_name} · ${selected.land_id}` : selectedMapCandidate ? selectedMapCandidate.land_id : "候选地空间分布"}</h2></div>
          {selectedMapCandidate && <button type="button" onClick={() => setSelectedId(null)}><Icon name="expand"/>查看全县</button>}
        </div>
        <CandidateMap
          candidates={mapCandidates}
          selected={selectedMapCandidate}
          onSelect={setSelectedId}
          resourceKey={resourceKey}
          onResourceKeyChange={selectResourceKey}
        />
        {selected ? <div className={styles.mapDetail}>
          <div><Icon name="pin"/><span>当前聚焦</span><strong>#{selected.rank} · {selected.area_mu.toFixed(1)} 亩</strong></div>
          <dl>
            <div><dt>距高速出入口</dt><dd>{selected.highway_exit_km.toFixed(2)} km</dd></div>
            <div><dt>距居民点</dt><dd>{selected.settlement_km.toFixed(2)} km</dd></div>
            <div><dt>距河流</dt><dd>{selected.river_km.toFixed(2)} km</dd></div>
            <div><dt>租金代理</dt><dd>{RENT_LABELS[selected.residential_rent_source_level] || selected.residential_rent_source_level}</dd></div>
          </dl>
        </div> : selectedMapCandidate ? <div className={styles.mapDetail}><div><Icon name="pin"/><span>当前候选斑块</span><strong>{selectedMapCandidate.area_mu.toFixed(1)} 亩 · 等待 ADS 指标</strong></div></div> : <p className={styles.mapCaption}>点击左侧候选卡片或地图斑块，可聚焦查看候选地。</p>}
      </section>
    </div>
  </main>;
}
