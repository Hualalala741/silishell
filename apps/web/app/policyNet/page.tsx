import PolicyNetwork, { type PolicyMeasure } from "./PolicyNetwork";

export const dynamic = "force-dynamic";

const API_URL = process.env.INTERNAL_API_URL
  || process.env.NEXT_PUBLIC_API_URL
  || "http://127.0.0.1:8000";

async function loadMeasures(): Promise<{ measures: PolicyMeasure[]; runName: string }> {
  try {
    const response = await fetch(`${API_URL}/policy-network?topic_key=zhuchanye`, { cache: "no-store" });
    if (!response.ok) throw new Error(`API ${response.status}`);
    return await response.json() as { measures: PolicyMeasure[]; runName: string };
  } catch {
    return { measures: [], runName: "无法读取 PostgreSQL 政策画像" };
  }
}

export default async function PolicyNetworkPage({ searchParams }: { searchParams: Promise<{ measure?: string }> }) {
  const data = await loadMeasures();
  const { measure } = await searchParams;
  return <PolicyNetwork {...data} initialMeasureId={measure} />;
}
