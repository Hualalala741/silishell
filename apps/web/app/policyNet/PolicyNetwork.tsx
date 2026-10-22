"use client";

import type { CSSProperties, MouseEvent } from "react";
import { useEffect, useMemo, useRef, useState } from "react";

import styles from "./policy-net.module.css";

type Quantity = {
  metric?: string; raw_text?: string; value?: number; unit?: string; dimension?: string;
  normalized_value?: number; normalized_unit?: string;
};

export type PolicyMeasure = {
  id: string; toolId: number; secondaryToolId: number | null; segments: string[]; text: string;
  evidence: string; stage: string; score: number; strengthScore: number; specificityScore: number;
  scoreVersion: string; scoreReasons: Record<string, string[]>; documentId: string; sectionId: string;
  sectionPath: string[]; sourceUrl: string; documentTitle: string; publishedAt: string; sourceType: string;
  publisher: string; documentNumber: string; region: string; topic: string; recordType: string;
  targets: string[]; agencies: string[]; timeScope: string; startDate: string; endDate: string;
  quantities: Quantity[]; dedupGroupId: string; isPrimary: boolean; duplicateOf: string;
};

type Props = { measures: PolicyMeasure[]; runName: string; initialMeasureId?: string };
type Level = "tools" | "segments" | "measures" | "detail";
type Phase = "idle" | "focusing" | "leaving" | "entering";
type PositionedNode = {
  id: string; label: string; count?: number; score?: number; x: number; y: number;
  size: number; color: string; measure?: PolicyMeasure;
};

const TOOLS: Record<number, string> = {
  1: "财政支持", 2: "金融支持", 3: "土地与林地保障", 4: "基础设施建设",
  5: "技术创新与标准", 6: "人才培养", 7: "市场推广与应用示范",
  8: "企业、园区与产业组织", 9: "资源培育与生态管护",
  10: "行政审批与政务服务", 99: "其他",
};

const COLORS = ["#20836f", "#d36f4f", "#477da9", "#8b69a4", "#bd8a35", "#547f72"];
const STAGE_NAMES: Record<string, string> = {
  planned: "计划中", requested: "申请 / 争取", allocated: "资金已分解", disbursed: "已拨付",
  ongoing: "执行中", completed: "已完成", verified: "已验收",
  not_applicable: "制度规则", unknown: "状态未知",
};
const RECORD_NAMES: Record<string, string> = {
  target: "规划目标", policy_action: "政策行动", implementation: "实施进展",
  result: "实施结果", rule: "制度规则", unknown: "其他记录",
};

function colorFor(toolId: number) { return COLORS[toolId === 99 ? 5 : (toolId - 1) % COLORS.length]; }
function shortLabel(value: string, max = 16) { return value.length > max ? `${value.slice(0, max - 1)}…` : value; }
function valueOrDash(value: string) { return value || "—"; }

function radialPositions(count: number) {
  const outerCount = count > 8 ? Math.ceil(count * 0.6) : count;
  return Array.from({ length: count }, (_, index) => {
    const inner = index >= outerCount;
    const ringCount = inner ? count - outerCount : outerCount;
    const ringIndex = inner ? index - outerCount : index;
    const radiusX = inner ? 27 : 43;
    const radiusY = inner ? 25 : 39;
    const offset = inner ? Math.PI / Math.max(ringCount, 1) : 0;
    const angle = -Math.PI / 2 + (Math.PI * 2 * ringIndex) / Math.max(ringCount, 1) + offset;
    return { x: 50 + Math.cos(angle) * radiusX, y: 50 + Math.sin(angle) * radiusY };
  });
}

function nodeStyle(node: PositionedNode, index: number) {
  return {
    "--node-x": `${node.x}%`, "--node-y": `${node.y}%`, "--node-size": `${node.size}px`,
    "--node-color": node.color, "--node-index": index,
  } as CSSProperties;
}

export default function PolicyNetwork({ measures, runName, initialMeasureId }: Props) {
  const initialMeasure = measures.find((item) => item.id === initialMeasureId) || null;
  const [level, setLevel] = useState<Level>(initialMeasure ? "detail" : "tools");
  const [toolId, setToolId] = useState<number | null>(initialMeasure?.toolId || null);
  const [segment, setSegment] = useState<string | null>(initialMeasure?.segments[0] || null);
  const [selected, setSelected] = useState<PolicyMeasure | null>(initialMeasure);
  const [phase, setPhase] = useState<Phase>("idle");
  const [focusId, setFocusId] = useState<string | null>(null);
  const [focusVector, setFocusVector] = useState({ x: 0, y: 0 });
  const timers = useRef<number[]>([]);
  const graphRef = useRef<HTMLDivElement>(null);

  const toolRows = useMemo(() => Object.entries(TOOLS)
    .map(([id, label]) => ({ id: Number(id), label, count: measures.filter((item) => item.toolId === Number(id)).length }))
    .filter((item) => item.count > 0).sort((a, b) => b.count - a.count), [measures]);

  const segmentRows = useMemo(() => {
    const counts = new Map<string, number>();
    measures.filter((item) => item.toolId === toolId).forEach((item) => {
      item.segments.forEach((name) => counts.set(name, (counts.get(name) || 0) + 1));
    });
    return Array.from(counts, ([label, count]) => ({ label, count })).sort((a, b) => b.count - a.count);
  }, [measures, toolId]);

  const measureRows = useMemo(() => measures
    .filter((item) => item.toolId === toolId && item.segments.includes(segment || ""))
    .sort((a, b) => b.score - a.score), [measures, segment, toolId]);

  const nodes = useMemo<PositionedNode[]>(() => {
    if (level === "tools") {
      const max = Math.max(...toolRows.map((item) => item.count), 1);
      const positions = radialPositions(toolRows.length);
      return toolRows.map((item, index) => ({ id: `tool-${item.id}`, label: item.label, count: item.count,
        ...positions[index], size: 76 + 18 * Math.sqrt(item.count / max), color: colorFor(item.id) }));
    }
    if (level === "segments") {
      const max = Math.max(...segmentRows.map((item) => item.count), 1);
      const positions = radialPositions(segmentRows.length);
      return segmentRows.map((item, index) => ({ id: `segment-${item.label}`, label: item.label, count: item.count,
        ...positions[index], size: 78 + 17 * Math.sqrt(item.count / max), color: colorFor(toolId || 1) }));
    }
    const positions = radialPositions(measureRows.length);
    return measureRows.map((item, index) => ({ id: item.id, label: item.text, score: item.score,
      ...positions[index], size: 68 + item.score * 0.2, color: colorFor(item.toolId), measure: item }));
  }, [level, measureRows, segmentRows, toolId, toolRows]);

  useEffect(() => () => timers.current.forEach(window.clearTimeout), []);
  function clearTimers() { timers.current.forEach(window.clearTimeout); timers.current = []; }

  function drill(event: MouseEvent<HTMLButtonElement>, id: string, applyNext: () => void) {
    if (phase !== "idle") return;
    clearTimers();
    const graph = graphRef.current?.getBoundingClientRect();
    const target = event.currentTarget.getBoundingClientRect();
    if (graph) setFocusVector({
      x: graph.left + graph.width / 2 - (target.left + target.width / 2),
      y: graph.top + graph.height / 2 - (target.top + target.height / 2),
    });
    setFocusId(id); setPhase("focusing");
    timers.current.push(window.setTimeout(() => setPhase("leaving"), 430));
    timers.current.push(window.setTimeout(() => {
      applyNext(); setFocusId(null); setFocusVector({ x: 0, y: 0 }); setPhase("entering");
      timers.current.push(window.setTimeout(() => setPhase("idle"), 70));
    }, 720));
  }

  function resetMotion() { clearTimers(); setPhase("idle"); setFocusId(null); setFocusVector({ x: 0, y: 0 }); }
  function goRoot() { resetMotion(); setToolId(null); setSegment(null); setSelected(null); setLevel("tools"); }
  function goSegments() { resetMotion(); setSegment(null); setSelected(null); setLevel("segments"); }
  function goMeasures() { resetMotion(); setSelected(null); setLevel("measures"); }
  function goBack() {
    if (level === "detail") goMeasures();
    else if (level === "measures") goSegments();
    else goRoot();
  }

  const hubLabel = level === "tools" ? "竹产业" : level === "segments" ? TOOLS[toolId || 1] : segment || "原子政策";
  const hubCount = level === "tools" ? measures.length : level === "segments"
    ? measures.filter((item) => item.toolId === toolId).length : measureRows.length;
  const sceneStyle = { "--focus-x": `${focusVector.x}px`, "--focus-y": `${focusVector.y}px` } as CSSProperties;
  const instruction = level === "tools" ? "选择一个政策工具" : level === "segments" ? "选择一个产业环节"
    : level === "measures" ? "选择原子政策，展开完整信息" : "原子政策详情";

  return <main className="shell">
    <section className={styles.hero}>
      <div><p className="eyebrow">YONGCHUN · BAMBOO POLICY ATLAS</p><h1>永春县竹产业<br/><em>政策知识图谱</em></h1>
        <p>从工具、产业环节进入原子政策，沿着证据链回到每一份来源文件。</p></div>
      <div className={styles.heroStats}><span><strong>{measures.length}</strong>原子政策</span><span><strong>{toolRows.length}</strong>政策工具</span>
        <span><strong>{new Set(measures.flatMap((item) => item.segments)).size}</strong>产业环节</span></div>
    </section>

    <section className={`card ${styles.graphCard}`}>
      <header className={styles.graphHeader}>
        <div><div className={styles.breadcrumbs}><button onClick={goRoot}>竹产业</button>
          {toolId && <><b>/</b><button onClick={goSegments}>{TOOLS[toolId]}</button></>}
          {segment && <><b>/</b><button onClick={goMeasures}>{segment}</button></>}
          {selected && level === "detail" && <><b>/</b><span>{shortLabel(selected.text, 22)}</span></>}</div>
          <p><i className={styles.pulseDot}/>{instruction}</p></div>
        <button className={styles.backButton} onClick={goBack} disabled={level === "tools"}>← 返回上一层</button>
      </header>

      {measures.length === 0 ? <div className={styles.empty}>没有找到画像数据，请先生成竹产业画像。</div>
        : <div className={`${styles.graph} ${level === "detail" ? styles.graphDetail : ""}`} ref={graphRef}>
          {level === "detail" && selected ? <MeasureCard measure={selected} onClose={goMeasures}/>
            : <div className={`${styles.scene} ${phase === "idle" ? "" : styles[phase]}`} style={sceneStyle}>
              <div className={styles.ambientOrb}/><div className={styles.orbitOne}/><div className={styles.orbitTwo}/>
              <svg className={styles.edges} viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
                <defs><linearGradient id="edge-gradient"><stop offset="0" stopColor="#2f806d"/><stop offset="1" stopColor="#97b9ae"/></linearGradient></defs>
                {nodes.map((node) => <g key={node.id}><line className={styles.edgeGlow} x1="50" y1="50" x2={node.x} y2={node.y}/>
                  <line className={styles.edgeCore} x1="50" y1="50" x2={node.x} y2={node.y}/></g>)}
              </svg>
              <div className={styles.hub}><small>当前节点</small><strong>{shortLabel(hubLabel, 10)}</strong><span>{hubCount} 条</span></div>
              {nodes.map((node, index) => <button key={node.id} type="button"
                className={`${styles.node} ${node.measure ? styles.measureNode : ""} ${focusId === node.id ? styles.focusNode : ""}`}
                data-stage={node.measure?.stage} style={nodeStyle(node, index)} title={node.label}
                onClick={(event) => {
                  if (level === "tools") { const nextTool = Number(node.id.replace("tool-", "")); drill(event, node.id, () => { setToolId(nextTool); setLevel("segments"); }); }
                  else if (level === "segments") drill(event, node.id, () => { setSegment(node.label); setLevel("measures"); });
                  else if (node.measure) { const measure = node.measure; drill(event, node.id, () => { setSelected(measure); setLevel("detail"); }); }
                }}><span>{shortLabel(node.label, node.measure ? 15 : 10)}</span>
                <small>{node.measure ? `${node.score} 分` : `${node.count} 条`}<b>↗</b></small></button>)}
            </div>}
        </div>}
    </section>
    <footer className={styles.dataFoot}>DATASET · {runName}</footer>
  </main>;
}

function MeasureCard({ measure, onClose }: { measure: PolicyMeasure; onClose: () => void }) {
  const reasons = [...(measure.scoreReasons.strength || []), ...(measure.scoreReasons.specificity || [])];
  return <article className={styles.measureCard} aria-live="polite"><div className={styles.cardGlow}/>
    <header className={styles.cardTop}><div className={styles.cardBadges}><span className={styles.stageBadge}>{STAGE_NAMES[measure.stage] || measure.stage}</span>
      <span>{RECORD_NAMES[measure.recordType] || measure.recordType}</span><span>{TOOLS[measure.toolId]}</span></div>
      <button onClick={onClose} aria-label="关闭政策详情">×</button></header>
    <div className={styles.cardGrid}><div className={styles.cardMain}>
      <p className={styles.cardKicker}>ATOMIC POLICY · {measure.id}</p><h2>{measure.text}</h2>
      <div className={styles.evidenceBlock}><span>证据原文</span><blockquote>{measure.evidence}</blockquote></div>
      {measure.quantities.length > 0 && <section className={styles.cardSection}><h3>量化指标</h3><div className={styles.metrics}>
        {measure.quantities.map((quantity, index) => <div key={`${quantity.raw_text}-${index}`}><span>{quantity.dimension || quantity.metric || "指标"}</span>
          <strong>{quantity.raw_text || `${quantity.value ?? "—"}${quantity.unit || ""}`}</strong>
          {quantity.normalized_value != null && <small>标准值 {quantity.normalized_value.toLocaleString()} {quantity.normalized_unit}</small>}</div>)}
      </div></section>}
      <section className={styles.cardSection}><h3>政策对象与执行</h3><InfoRow label="政策对象" values={measure.targets}/>
        <InfoRow label="执行主体" values={measure.agencies}/><InfoRow label="产业环节" values={measure.segments}/>
        <InfoRow label="时间范围" values={[measure.timeScope || [measure.startDate, measure.endDate].filter(Boolean).join(" — ")].filter(Boolean)}/></section>
      <section className={styles.sourceCard}><div><span>来源文件 / 新闻</span><h3>{measure.documentTitle}</h3></div><dl>
        <div><dt>来源类型</dt><dd>{valueOrDash(measure.sourceType)}</dd></div><div><dt>发布机构</dt><dd>{valueOrDash(measure.publisher)}</dd></div>
        <div><dt>发布时间</dt><dd>{valueOrDash(measure.publishedAt)}</dd></div><div><dt>文号</dt><dd>{valueOrDash(measure.documentNumber)}</dd></div></dl>
        {measure.sourceUrl && <a href={measure.sourceUrl} target="_blank" rel="noreferrer">打开政府网站原文 <b>↗</b></a>}</section>
    </div><aside className={styles.cardAside}>
      <div className={styles.scoreDial} style={{ "--score": `${measure.score * 3.6}deg` } as CSSProperties}><div><strong>{measure.score}</strong><span>设计分</span></div></div>
      <div className={styles.scoreBars}><ScoreBar label="政策力度" value={measure.strengthScore}/><ScoreBar label="具体程度" value={measure.specificityScore}/></div>
      {reasons.length > 0 && <div className={styles.reasonList}><span>评分依据</span>{[...new Set(reasons)].map((reason) => <p key={reason}>✓ {reason}</p>)}</div>}
      <dl className={styles.metaList}><div><dt>区域</dt><dd>{measure.region}</dd></div><div><dt>产业主题</dt><dd>{measure.topic}</dd></div>
        <div><dt>文档 ID</dt><dd>{measure.documentId}</dd></div><div><dt>段落 ID</dt><dd>{measure.sectionId}</dd></div>
        <div><dt>去重组</dt><dd>{measure.dedupGroupId}</dd></div><div><dt>主记录</dt><dd>{measure.isPrimary ? "是" : "否"}</dd></div>
        {measure.duplicateOf && <div><dt>重复于</dt><dd>{measure.duplicateOf}</dd></div>}</dl>
    </aside></div>
    {measure.sectionPath.length > 0 && <footer className={styles.sectionPath}><span>原文位置</span>
      {measure.sectionPath.map((part, index) => <span key={`${part}-${index}`}>{part}</span>)}</footer>}
  </article>;
}

function InfoRow({ label, values }: { label: string; values: string[] }) {
  return <div className={styles.infoRow}><span>{label}</span><div>{values.length ? values.map((value) => <b key={value}>{value}</b>) : <em>未提取</em>}</div></div>;
}

function ScoreBar({ label, value }: { label: string; value: number }) {
  return <div><span>{label}<b>{value} / 5</b></span><i><b style={{ width: `${Math.min(value, 5) * 20}%` }}/></i></div>;
}
