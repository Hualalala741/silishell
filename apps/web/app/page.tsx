"use client";

import { useEffect, useMemo, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

type Topic = {
  key: string;
  name: string;
  queries: string[];
  subject_terms: string[];
  context_terms: string[];
  exclude_terms: string[];
  minimum_relevance_score: number;
};

type Candidate = {
  topic_key: string;
  topic: string;
  document_id: string;
  title: string;
  source_url: string;
  published_at: string;
  source_type: string;
  kind: string;
  template: string;
  document_number: string;
  publisher: string;
  channel: string;
  snippet: string;
  matched_queries: string[];
  subject_hits: string[];
  context_hits: string[];
  relevance_score: number;
};

type SearchResponse = {
  candidate_count: number;
  candidates: Candidate[];
};

type CrawlResponse = {
  run_id: number;
  storage: string;
  requested_count: number;
  success_count: number;
  updates: { source_url: string; status: string; error?: string | null }[];
};

type FormState = {
  requirementName: string;
  queries: string;
  subjectTerms: string;
  contextTerms: string;
  excludeTerms: string;
  score: number;
  startDate: string;
  endDate: string;
};

function isoDate(value: Date) {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function initialDates() {
  const end = new Date();
  const start = new Date(end);
  start.setFullYear(start.getFullYear() - 2);
  return { startDate: isoDate(start), endDate: isoDate(end) };
}

function terms(value: string) {
  return Array.from(new Set(value.split(/[，,、\n]/).map((item) => item.trim()).filter(Boolean)));
}

function termString(items: string[]) {
  return items.join("，");
}

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...options?.headers },
    cache: "no-store",
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = typeof body.detail === "string" ? body.detail : "请求失败，请检查输入后重试";
    throw new Error(detail);
  }
  return response.json();
}

function Icon({ name }: { name: "search" | "settings" | "list" | "download" | "check" | "calendar" | "edit" }) {
  const paths = {
    search: <><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/></>,
    settings: <><path d="M12 2v3M12 19v3M4.93 4.93l2.12 2.12M16.95 16.95l2.12 2.12M2 12h3M19 12h3M4.93 19.07l2.12-2.12M16.95 7.05l2.12-2.12"/><circle cx="12" cy="12" r="4"/></>,
    list: <><path d="M9 6h11M9 12h11M9 18h11"/><path d="M4 6h.01M4 12h.01M4 18h.01"/></>,
    download: <><path d="M12 3v12M7 10l5 5 5-5"/><path d="M5 21h14"/></>,
    check: <path d="m5 12 4 4L19 6"/>,
    calendar: <><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 10h18"/></>,
    edit: <><path d="m4 20 4.5-1 10-10a2.1 2.1 0 0 0-3-3l-10 10L4 20Z"/><path d="m13.5 7.5 3 3"/></>,
  };
  return <svg className="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">{paths[name]}</svg>;
}

function Field({ label, hint, value, onChange, tone }: { label: string; hint: string; value: string; onChange: (value: string) => void; tone?: "danger" }) {
  const count = terms(value).length;
  return <label className={`keyword-field ${tone === "danger" ? "danger" : ""}`}>
    <div className="label-row"><span>{label}</span><small>{count} 个词</small></div>
    <input value={value} onChange={(event) => onChange(event.target.value)} placeholder="用逗号分隔多个关键词" />
    <em>{hint}</em>
  </label>;
}

export default function Home() {
  const dates = useMemo(initialDates, []);
  const [topics, setTopics] = useState<Topic[]>([]);
  const [activeTopic, setActiveTopic] = useState("");
  const [form, setForm] = useState<FormState>({
    requirementName: "",
    queries: "",
    subjectTerms: "",
    contextTerms: "",
    excludeTerms: "",
    score: 45,
    ...dates,
  });
  const [results, setResults] = useState<Candidate[]>([]);
  const [selectedUrls, setSelectedUrls] = useState<Set<string>>(new Set());
  const [searching, setSearching] = useState(false);
  const [crawling, setCrawling] = useState(false);
  const [searched, setSearched] = useState(false);
  const [error, setError] = useState("");
  const [crawlResult, setCrawlResult] = useState<CrawlResponse | null>(null);

  useEffect(() => {
    api<Topic[]>("/topics")
      .then((items) => {
        setTopics(items);
        if (items[0]) applyTopic(items[0]);
      })
      .catch((cause) => {
        setError(cause instanceof Error ? cause.message : "无法连接后端");
      });
  }, []);

  function applyTopic(topic: Topic) {
    setActiveTopic(topic.key);
    setForm((current) => ({
      ...current,
      requirementName: topic.name,
      queries: termString(topic.queries),
      subjectTerms: termString(topic.subject_terms),
      contextTerms: termString(topic.context_terms),
      excludeTerms: termString(topic.exclude_terms),
      score: topic.minimum_relevance_score,
    }));
    setResults([]);
    setSelectedUrls(new Set());
    setSearched(false);
    setCrawlResult(null);
  }

  function patchForm(patch: Partial<FormState>) {
    setForm((current) => ({ ...current, ...patch }));
  }

  async function startSearch() {
    if (!form.requirementName.trim()) return setError("请先填写需求名称");
    if (!terms(form.queries).length || !terms(form.subjectTerms).length || !terms(form.contextTerms).length) {
      return setError("检索词、主题词和场景词都至少需要一个关键词");
    }
    setSearching(true);
    setError("");
    setCrawlResult(null);
    try {
      const response = await api<SearchResponse>("/search", {
        method: "POST",
        body: JSON.stringify({
          topic_key: activeTopic,
          requirement_name: form.requirementName,
          queries: terms(form.queries),
          subject_terms: terms(form.subjectTerms),
          context_terms: terms(form.contextTerms),
          exclude_terms: terms(form.excludeTerms),
          minimum_relevance_score: form.score,
          start_date: form.startDate,
          end_date: form.endDate,
        }),
      });
      setResults(response.candidates);
      setSelectedUrls(new Set(response.candidates.map((item) => item.source_url)));
      setSearched(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "检索失败");
    } finally {
      setSearching(false);
    }
  }

  function toggle(url: string) {
    setSelectedUrls((current) => {
      const next = new Set(current);
      next.has(url) ? next.delete(url) : next.add(url);
      return next;
    });
  }

  function toggleAll() {
    setSelectedUrls((current) => current.size === results.length ? new Set() : new Set(results.map((item) => item.source_url)));
  }

  async function startCrawl() {
    const selected = results.filter((item) => selectedUrls.has(item.source_url));
    if (!selected.length) return setError("请至少选择一条搜索结果");
    setCrawling(true);
    setError("");
    try {
      const response = await api<CrawlResponse>("/crawl", {
        method: "POST",
        body: JSON.stringify({
          topic_key: activeTopic,
          requirement_name: form.requirementName,
          candidates: selected,
          concurrency: 2,
          queries: terms(form.queries),
          subject_terms: terms(form.subjectTerms),
          context_terms: terms(form.contextTerms),
          exclude_terms: terms(form.excludeTerms),
          minimum_relevance_score: form.score,
          start_date: form.startDate,
        }),
      });
      setCrawlResult(response);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "抓取失败");
    } finally {
      setCrawling(false);
    }
  }

  const step = crawlResult ? 3 : searched ? 2 : 1;

  return <main className="shell">
    <section className="hero">
      <div>
        <p className="eyebrow">YONGCHUN · POLICY INTELLIGENCE</p>
        <h1>永春县<br/><em>政策采集</em></h1>
        <p className="hero-copy">调整检索范围与关键词，预览并筛选搜索结果，确认后再把正文入库。</p>
      </div>
      <div className="stepper" aria-label="采集进度">
        {[{n: 1, title: "配置检索", text: "确认关键词与时间"}, {n: 2, title: "筛选结果", text: "查看分数并勾选"}, {n: 3, title: "抓取正文", text: "正文写入数据库"}].map((item, index) =>
          <div className={`step ${step >= item.n ? "active" : ""}`} key={item.n}>
            <span>{step > item.n ? <Icon name="check"/> : item.n}</span>
            <div><strong>{item.title}</strong><small>{item.text}</small></div>
            {index < 2 && <b />}
          </div>
        )}
      </div>
    </section>

    {error && <div className="alert"><strong>操作未完成</strong><span>{error}</span><button onClick={() => setError("")} aria-label="关闭">×</button></div>}

    <div className="workspace">
      <aside className="sidebar card">
        <div className="section-kicker"><Icon name="settings"/><span>需求模板</span></div>
        <h2>选择采集主题</h2>
        <div className="topic-list">
          {topics.map((topic) => <button className={activeTopic === topic.key ? "active" : ""} onClick={() => applyTopic(topic)} key={topic.key}>
            <span>{topic.name.slice(0, 1)}</span>
            <div><strong>{topic.name}</strong><small>{topic.queries.length} 个检索词</small></div>
            <b>›</b>
          </button>)}
        </div>
        <div className="tip">
          <span>i</span>
          <div><strong>关键词如何生效？</strong><p>页面需同时命中主题词和场景词；排除词会直接过滤结果。</p></div>
        </div>
      </aside>

      <div className="main-column">
        <section className="card config-card">
          <div className="card-heading">
            <div><span className="heading-icon"><Icon name="edit"/></span><div><p>STEP 01</p><h2>确认检索条件</h2></div></div>
          </div>

          <div className="form-grid">
            <label className="requirement-name"><span>需求名称</span><input value={form.requirementName} onChange={(e) => patchForm({ requirementName: e.target.value })} placeholder="例如：竹产业政策"/><em>将作为本次数据库运行记录的名称</em></label>
            <div className="date-field">
              <div className="label-row"><span>搜索时间</span><small><Icon name="calendar"/> 自定义范围</small></div>
              <div><input type="date" value={form.startDate} onChange={(e) => patchForm({ startDate: e.target.value })}/><b>至</b><input type="date" value={form.endDate} onChange={(e) => patchForm({ endDate: e.target.value })}/></div>
            </div>
          </div>

          <div className="keywords-grid">
            <Field label="检索词" hint="逐个调用站内搜索，扩大候选范围" value={form.queries} onChange={(queries) => patchForm({ queries })}/>
            <Field label="主题词" hint="结果中至少命中一个，定义要找什么" value={form.subjectTerms} onChange={(subjectTerms) => patchForm({ subjectTerms })}/>
            <Field label="场景词" hint="结果中至少命中一个，限定政策语境" value={form.contextTerms} onChange={(contextTerms) => patchForm({ contextTerms })}/>
            <Field label="排除词" hint="命中任意一个就从结果中排除" value={form.excludeTerms} onChange={(excludeTerms) => patchForm({ excludeTerms })} tone="danger"/>
          </div>

          <div className="score-row">
            <label><span>最低相关分</span><strong>{form.score}</strong></label>
            <input type="range" min="0" max="80" step="5" value={form.score} onChange={(e) => patchForm({ score: Number(e.target.value) })}/>
            <small>分数越高，结果越聚焦</small>
          </div>

          <div className="confirm-bar">
            <p><Icon name="check"/><span><strong>检索前确认</strong>：将使用 {terms(form.queries).length} 个检索词，排除 {terms(form.excludeTerms).length} 个词</span></p>
            <button className="primary" onClick={startSearch} disabled={searching || crawling}>{searching ? <><span className="spinner"/>正在检索…</> : <><Icon name="search"/>确认关键词并开始检索</>}</button>
          </div>
        </section>

        <section className={`card results-card ${!searched ? "waiting" : ""}`}>
          <div className="card-heading compact">
            <div><span className="heading-icon coral"><Icon name="list"/></span><div><p>STEP 02</p><h2>筛选搜索结果</h2></div></div>
            {searched && <span className="result-count">找到 <strong>{results.length}</strong> 条</span>}
          </div>

          {!searched ? <div className="empty-state"><span><Icon name="search"/></span><strong>等待检索</strong><p>确认上方关键词后，候选政策会显示在这里。</p></div> : <>
            <div className="result-toolbar">
              <label><input type="checkbox" checked={results.length > 0 && selectedUrls.size === results.length} onChange={toggleAll}/><span>全选结果</span></label>
              <span>已选择 <strong>{selectedUrls.size}</strong> / {results.length} 条</span>
            </div>
            <div className="result-window">
              {results.length === 0 ? <div className="no-results"><strong>没有符合条件的结果</strong><p>可以降低最低相关分、扩展时间范围或调整关键词后重试。</p></div> : results.map((item) =>
                <article className={`result-item ${selectedUrls.has(item.source_url) ? "selected" : ""}`} key={item.source_url}>
                  <label className="select-box"><input type="checkbox" checked={selectedUrls.has(item.source_url)} onChange={() => toggle(item.source_url)}/><span /></label>
                  <div className="result-body">
                    <div className="result-title"><a href={item.source_url} target="_blank" rel="noreferrer">{item.title}</a><span className={item.relevance_score >= 60 ? "high" : ""}>{item.relevance_score}<small>相关分</small></span></div>
                    <p>{item.snippet || "暂无摘要"}</p>
                    <div className="meta"><span>{item.source_type || item.kind}</span><span>{item.published_at || "日期未知"}</span>{item.publisher && <span>{item.publisher}</span>}</div>
                    <div className="hits">{item.subject_hits.slice(0, 3).map((hit) => <i key={hit}>#{hit}</i>)}{item.context_hits.slice(0, 2).map((hit) => <i key={hit}>#{hit}</i>)}</div>
                  </div>
                </article>)}
            </div>
            <div className="crawl-bar">
              <div><strong>确认抓取所选正文</strong><span>文件将保存到 data/pages/需求名_日期_时间/</span></div>
              <button className="primary coral-button" onClick={startCrawl} disabled={crawling || selectedUrls.size === 0}>{crawling ? <><span className="spinner"/>正在抓取 {selectedUrls.size} 条…</> : <><Icon name="download"/>确定并抓取文本</>}</button>
            </div>
          </>}
        </section>

        {crawlResult && <section className="card success-card">
          <span><Icon name="check"/></span>
          <div><p>STEP 03 · 抓取完成</p><h2>成功入库 {crawlResult.success_count} / {crawlResult.requested_count} 条正文</h2><code>PostgreSQL 运行编号 #{crawlResult.run_id}</code></div>
        </section>}
      </div>
    </div>
  </main>;
}
