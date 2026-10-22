import type { Metadata } from "next";
import AppShell from "./AppShell";
import "./globals.css";

export const metadata: Metadata = {
  title: "政策采集工作台",
  description: "确认关键词、检索政策并抓取正文",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body><AppShell>{children}</AppShell></body>
    </html>
  );
}
