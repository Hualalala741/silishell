import type { Metadata } from "next";
import SiteSelection from "./SiteSelection";

export const metadata: Metadata = {
  title: "资源选址 · 政策采集工作台",
  description: "永春县候选资源筛选、排序与空间查看",
};

export default function SiteSelectionPage() {
  return <SiteSelection/>;
}
