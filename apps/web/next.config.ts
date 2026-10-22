import type { NextConfig } from "next";
import fs from "node:fs";
import path from "node:path";

function readRootEnv(name: string) {
  if (process.env[name]) return process.env[name];
  const file = path.join(__dirname, "../../.env");
  if (!fs.existsSync(file)) return "";
  for (const line of fs.readFileSync(file, "utf8").split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const separator = trimmed.indexOf("=");
    if (separator === -1) continue;
    if (trimmed.slice(0, separator).trim() !== name) continue;
    let value = trimmed.slice(separator + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    return value;
  }
  return "";
}

const amapKey = readRootEnv("NEXT_PUBLIC_AMAP_KEY");
const amapSecurityCode = readRootEnv("NEXT_PUBLIC_AMAP_SECURITY_JS_CODE");
process.env.NEXT_PUBLIC_AMAP_KEY = amapKey;
process.env.NEXT_PUBLIC_AMAP_SECURITY_JS_CODE = amapSecurityCode;

const nextConfig: NextConfig = {
  output: "standalone",
  env: {
    NEXT_PUBLIC_AMAP_KEY: amapKey,
    NEXT_PUBLIC_AMAP_SECURITY_JS_CODE: amapSecurityCode,
  },
};

export default nextConfig;
