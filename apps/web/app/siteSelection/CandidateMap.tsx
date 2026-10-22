"use client";

import { useEffect, useRef, useState } from "react";
import styles from "./site-selection.module.css";

export type MapCandidate = {
  land_id: string;
  area_mu: number;
  rank: number | null;
  longitude: number;
  latitude: number;
  geometry: {
    type: "Polygon" | "MultiPolygon";
    coordinates: number[][][] | number[][][][];
  };
};

type ProtectedAreaFeature = {
  type: "Feature";
  properties: {
    name?: string;
    boundary_role?: string;
    accuracy_note?: string;
  };
  geometry: {
    type: "Polygon" | "MultiPolygon";
    coordinates: number[][][] | number[][][][];
  };
};

type ProtectedAreaCollection = {
  type: "FeatureCollection";
  features: ProtectedAreaFeature[];
};

type AMapOverlay = { on: (event: string, handler: () => void) => void };
type AMapMap = {
  add: (overlays: AMapOverlay[]) => void;
  remove: (overlays: AMapOverlay[]) => void;
  destroy: () => void;
  setFitView: (overlays: AMapOverlay[], immediately: boolean, avoid: number[]) => void;
  setZoomAndCenter: (zoom: number, center: [number, number]) => void;
};

declare global {
  interface Window {
    _AMapSecurityConfig?: { securityJsCode: string };
    AMap?: {
      Map: new (container: HTMLElement, options: Record<string, unknown>) => AMapMap;
      TileLayer: { Satellite: new () => unknown; RoadNet: new () => unknown };
      Marker: new (options: Record<string, unknown>) => AMapOverlay;
      Polygon: new (options: Record<string, unknown>) => AMapOverlay;
    };
  }
}

const YONGCHUN_CENTER: [number, number] = [118.295, 25.322];
let amapLoader: Promise<void> | null = null;

function loadAmapScript(key: string) {
  if (window.AMap) return Promise.resolve();
  if (!amapLoader) {
    amapLoader = new Promise<void>((resolve, reject) => {
      const script = document.createElement("script");
      script.src = `https://webapi.amap.com/maps?v=2.0&key=${encodeURIComponent(key)}`;
      script.async = true;
      script.onload = () => resolve();
      script.onerror = () => {
        amapLoader = null;
        reject(new Error("高德地图脚本加载失败，请检查网络和 Key 的域名白名单"));
      };
      document.head.appendChild(script);
    });
  }
  return amapLoader;
}

function outOfChina(longitude: number, latitude: number) {
  return longitude < 72.004 || longitude > 137.8347 || latitude < 0.8293 || latitude > 55.8271;
}

function transformLatitude(longitude: number, latitude: number) {
  let value = -100 + 2 * longitude + 3 * latitude + 0.2 * latitude * latitude + 0.1 * longitude * latitude + 0.2 * Math.sqrt(Math.abs(longitude));
  value += (20 * Math.sin(6 * longitude * Math.PI) + 20 * Math.sin(2 * longitude * Math.PI)) * 2 / 3;
  value += (20 * Math.sin(latitude * Math.PI) + 40 * Math.sin(latitude / 3 * Math.PI)) * 2 / 3;
  value += (160 * Math.sin(latitude / 12 * Math.PI) + 320 * Math.sin(latitude * Math.PI / 30)) * 2 / 3;
  return value;
}

function transformLongitude(longitude: number, latitude: number) {
  let value = 300 + longitude + 2 * latitude + 0.1 * longitude * longitude + 0.1 * longitude * latitude + 0.1 * Math.sqrt(Math.abs(longitude));
  value += (20 * Math.sin(6 * longitude * Math.PI) + 20 * Math.sin(2 * longitude * Math.PI)) * 2 / 3;
  value += (20 * Math.sin(longitude * Math.PI) + 40 * Math.sin(longitude / 3 * Math.PI)) * 2 / 3;
  value += (150 * Math.sin(longitude / 12 * Math.PI) + 300 * Math.sin(longitude / 30 * Math.PI)) * 2 / 3;
  return value;
}

function wgs84ToGcj02(longitude: number, latitude: number): [number, number] {
  if (outOfChina(longitude, latitude)) return [longitude, latitude];
  const earthA = 6378245;
  const earthEe = 0.006693421622965943;
  let latitudeOffset = transformLatitude(longitude - 105, latitude - 35);
  let longitudeOffset = transformLongitude(longitude - 105, latitude - 35);
  const radianLatitude = latitude / 180 * Math.PI;
  let magic = Math.sin(radianLatitude);
  magic = 1 - earthEe * magic * magic;
  const sqrtMagic = Math.sqrt(magic);
  latitudeOffset = latitudeOffset * 180 / ((earthA * (1 - earthEe)) / (magic * sqrtMagic) * Math.PI);
  longitudeOffset = longitudeOffset * 180 / (earthA / sqrtMagic * Math.cos(radianLatitude) * Math.PI);
  return [longitude + longitudeOffset, latitude + latitudeOffset];
}

function markerElement(candidate: MapCandidate, active: boolean) {
  const element = document.createElement("div");
  const top = candidate.rank !== null && candidate.rank <= 5;
  element.className = [styles.marker, top ? styles.topMarker : "", active ? styles.activeMarker : ""].filter(Boolean).join(" ");
  element.textContent = top ? String(candidate.rank) : "";
  element.title = `${candidate.land_id} · ${candidate.area_mu.toFixed(1)} 亩`;
  return element;
}

export default function CandidateMap({ candidates, selected, onSelect, resourceKey, onResourceKeyChange }: {
  candidates: MapCandidate[];
  selected?: MapCandidate;
  onSelect: (id: string) => void;
  resourceKey: "abandoned_cropland" | "stable_natural_resource";
  onResourceKeyChange: (resourceKey: "abandoned_cropland" | "stable_natural_resource") => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<AMapMap | null>(null);
  const overlaysRef = useRef<AMapOverlay[]>([]);
  const viewKeyRef = useRef("");
  const [status, setStatus] = useState<"loading" | "ready" | "missing" | "error">("loading");
  const [message, setMessage] = useState("");
  const [visibleLayer, setVisibleLayer] = useState<"none" | "niumulin">("none");
  const [protectedArea, setProtectedArea] = useState<ProtectedAreaFeature | null>(null);
  const [layerError, setLayerError] = useState("");
  const amapKey = process.env.NEXT_PUBLIC_AMAP_KEY || "";
  const securityCode = process.env.NEXT_PUBLIC_AMAP_SECURITY_JS_CODE || "";
  const candidateLayerLabel = resourceKey === "abandoned_cropland" ? "疑似撂荒耕地" : "稳定裸地/草灌地";

  useEffect(() => {
    if (visibleLayer !== "niumulin" || protectedArea) return;
    const controller = new AbortController();
    setLayerError("");
    fetch("/data/yongchun_niumulin_protected_area.geojson", { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error("保护区边界加载失败");
        return response.json() as Promise<ProtectedAreaCollection>;
      })
      .then((payload) => {
        const outerBoundary = payload.features.find(
          (feature) => feature.properties.boundary_role === "reserve_outer_boundary",
        );
        if (!outerBoundary) throw new Error("保护区数据缺少外围边界");
        setProtectedArea(outerBoundary);
      })
      .catch((reason: Error) => {
        if (reason.name !== "AbortError") setLayerError(reason.message);
      });
    return () => controller.abort();
  }, [protectedArea, visibleLayer]);

  useEffect(() => {
    if (!amapKey) {
      setStatus("missing");
      return;
    }
    if (securityCode) window._AMapSecurityConfig = { securityJsCode: securityCode };
    let cancelled = false;
    loadAmapScript(amapKey)
      .then(() => {
        if (cancelled || !containerRef.current || !window.AMap || mapRef.current) return;
        mapRef.current = new window.AMap.Map(containerRef.current, {
          zoom: 11,
          center: YONGCHUN_CENTER,
          layers: [new window.AMap.TileLayer.Satellite(), new window.AMap.TileLayer.RoadNet()],
        });
        setStatus("ready");
      })
      .catch((reason: Error) => {
        if (cancelled) return;
        setStatus("error");
        setMessage(reason.message);
      });
    return () => {
      cancelled = true;
      overlaysRef.current = [];
      mapRef.current?.destroy();
      mapRef.current = null;
    };
  }, [amapKey, securityCode]);

  useEffect(() => {
    const map = mapRef.current;
    const AMapNamespace = window.AMap;
    if (!map || !AMapNamespace || status !== "ready") return;
    if (overlaysRef.current.length) map.remove(overlaysRef.current);
    const polygons = candidates.flatMap((candidate) => {
      const active = selected?.land_id === candidate.land_id;
      const top = candidate.rank !== null && candidate.rank <= 5;
      const polygonCoordinates = candidate.geometry.type === "MultiPolygon"
        ? candidate.geometry.coordinates as number[][][][]
        : [candidate.geometry.coordinates as number[][][]];
      return polygonCoordinates.map((polygon) => {
        const overlay = new AMapNamespace.Polygon({
          path: polygon.map((ring) => ring.map(([longitude, latitude]) => wgs84ToGcj02(longitude, latitude))),
          strokeColor: active ? "#f59e0b" : top ? "#166534" : "#4b8b3b",
          strokeWeight: active ? 3 : top ? 2 : 1,
          strokeOpacity: active ? 1 : 0.85,
          fillColor: active ? "#fbbf24" : top ? "#22c55e" : "#86efac",
          fillOpacity: active ? 0.42 : top ? 0.30 : 0.18,
          zIndex: active ? 150 : top ? 110 : 60,
        });
        overlay.on("click", () => onSelect(candidate.land_id));
        return overlay;
      });
    });
    const markers = candidates.filter((candidate) => candidate.rank !== null).map((candidate) => {
      const active = selected?.land_id === candidate.land_id;
      const marker = new AMapNamespace.Marker({
        position: wgs84ToGcj02(candidate.longitude, candidate.latitude),
        anchor: "center",
        content: markerElement(candidate, active),
        zIndex: active ? 200 : candidate.rank !== null && candidate.rank <= 5 ? 120 : 80,
      });
      marker.on("click", () => onSelect(candidate.land_id));
      return marker;
    });
    const protectedPolygons = visibleLayer === "niumulin" && protectedArea
      ? (protectedArea.geometry.type === "MultiPolygon"
          ? protectedArea.geometry.coordinates as number[][][][]
          : [protectedArea.geometry.coordinates as number[][][]]
        ).map((polygon) => new AMapNamespace.Polygon({
          path: polygon.map((ring) => ring.map(([longitude, latitude]) => wgs84ToGcj02(longitude, latitude))),
          strokeColor: "#c026d3",
          strokeWeight: 4,
          strokeOpacity: 1,
          strokeStyle: "dashed",
          fillColor: "#e879f9",
          fillOpacity: 0.12,
          zIndex: 55,
        }))
      : [];
    const overlays = [...protectedPolygons, ...polygons, ...markers];
    if (overlays.length) map.add(overlays);
    overlaysRef.current = overlays;

    const viewKey = selected
      ? `focus:${selected.land_id}:${visibleLayer}`
      : `county:${visibleLayer}:${protectedPolygons.length}:${candidates.map((candidate) => candidate.land_id).join(",")}`;
    if (viewKey === viewKeyRef.current) return;
    viewKeyRef.current = viewKey;
    if (selected) map.setZoomAndCenter(16, wgs84ToGcj02(selected.longitude, selected.latitude));
    else if (polygons.length || protectedPolygons.length) map.setFitView([...polygons, ...protectedPolygons], false, [48, 48, 48, 48]);
  }, [candidates, onSelect, protectedArea, selected, status, visibleLayer]);

  return <div className={styles.mapFrame}>
    <div ref={containerRef} className={styles.map}/>
    {status === "ready" && <>
      <div className={styles.layerControl}>
        <strong>地图图层</strong>
        <label htmlFor="candidate-type-layer">地块类型</label>
        <select id="candidate-type-layer" value={resourceKey} onChange={(event) => onResourceKeyChange(event.target.value as "abandoned_cropland" | "stable_natural_resource")}>
          <option value="abandoned_cropland">疑似撂荒耕地</option>
          <option value="stable_natural_resource">稳定裸地/草灌地</option>
        </select>
        <label htmlFor="protected-area-layer">保护区图层</label>
        <select id="protected-area-layer" value={visibleLayer} onChange={(event) => setVisibleLayer(event.target.value as "none" | "niumulin")}>
          <option value="none">不显示保护区</option>
          <option value="niumulin">牛姆林保护区围栏</option>
        </select>
        {visibleLayer === "niumulin" && <small className={layerError ? styles.layerError : ""}>
          {layerError || (protectedArea ? "2018年公开坐标整理 · 近似边界" : "正在加载保护区边界…")}
        </small>}
      </div>
      <div className={styles.legend}>
        <span className={styles.candidateLegend}><i/>{candidateLayerLabel}</span>
        <span className={styles.topLegend}><i/>前 5 名</span>
        {visibleLayer === "niumulin" && protectedArea && <span className={styles.protectionLegend}><i/>保护区围栏</span>}
      </div>
    </>}
    {status !== "ready" && <div className={styles.mapStatus}>
      {status === "loading" && <span>正在加载高德卫星地图…</span>}
      {status === "missing" && <span>在仓库根目录 <b>.env</b> 填写 <b>NEXT_PUBLIC_AMAP_KEY</b> 后，重启前端即可显示卫星地图。</span>}
      {status === "error" && <span>{message}</span>}
    </div>}
  </div>;
}
