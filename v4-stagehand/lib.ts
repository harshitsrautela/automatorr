// Shared wiring for the Stagehand contender: model config (Gemini through the bench proxy), a call-counting middleware,
// and a Stagehand factory (local headless Chrome, env LOCAL, no Browserbase account).

import { Stagehand } from "@browserbasehq/stagehand";
import type { LogLine } from "@browserbasehq/stagehand";
import path from "node:path";
import fs from "node:fs";
import os from "node:os";
import { fileURLToPath } from "node:url";

export const HERE = path.dirname(fileURLToPath(import.meta.url));
export const BENCH = path.resolve(HERE, "..", "bench");

export const TOOL_TAG = "stagehand";
export const MODEL = process.env.STAGEHAND_MODEL || "google/gemini-3.5-flash-lite";
// AI SDK google provider: its default baseURL is https://generativelanguage.googleapis.com/v1beta, so the proxy URL
// keeps the /v1beta suffix. Key "bench" is the placeholder the proxy swaps for a real key.
export const BASE_URL = process.env.STAGEHAND_BASE_URL || `http://127.0.0.1:8899/${TOOL_TAG}/v1beta`;
export const API_KEY = "bench";

// Counts every model call Stagehand makes (agent steps, act/extract/observe inference, self-heal) via the
// documented `middleware` hook on the model config. `calls.n` is reset by the caller per record.
export const calls = { n: 0, byKind: { generate: 0, stream: 0 } as Record<string, number> };
const countingMiddleware = {
  wrapGenerate: async ({ doGenerate }: any) => {
    calls.n++;
    calls.byKind.generate++;
    return doGenerate();
  },
  wrapStream: async ({ doStream }: any) => {
    calls.n++;
    calls.byKind.stream++;
    return doStream();
  },
};

export function modelConfig() {
  return {
    modelName: MODEL,
    apiKey: API_KEY,
    baseURL: BASE_URL,
    middleware: countingMiddleware as any,
  };
}

export interface MakeOpts {
  cacheDir?: string; // undefined = Stagehand caching off
  headless?: boolean;
  verbose?: 0 | 1 | 2;
  onLog?: (l: LogLine) => void;
}

// Browser: Playwright's "Chrome for Testing" from ~/Library/Caches/ms-playwright (newest chromium-*). On this Mac the
// installed /Applications/Google Chrome.app exits by itself ~30 s after a headless launch (its updater), which kills
// the run mid-record, so it is not used. Override with STAGEHAND_CHROME=/path/to/chrome.
export function chromePath(): string | undefined {
  if (process.env.STAGEHAND_CHROME) return process.env.STAGEHAND_CHROME;
  const root = path.join(os.homedir(), "Library", "Caches", "ms-playwright");
  if (!fs.existsSync(root)) return undefined;
  const dirs = fs
    .readdirSync(root)
    .filter((d) => /^chromium-\d+$/.test(d))
    .sort((a, b) => Number(b.split("-")[1]) - Number(a.split("-")[1]));
  for (const d of dirs) {
    for (const sub of ["chrome-mac-arm64", "chrome-mac-x64", "chrome-mac"]) {
      const p = path.join(root, d, sub, "Google Chrome for Testing.app", "Contents", "MacOS", "Google Chrome for Testing");
      if (fs.existsSync(p)) return p;
    }
    const linux = path.join(root, d, "chrome-linux", "chrome");
    if (fs.existsSync(linux)) return linux;
  }
  return undefined;
}

export function makeStagehand(o: MakeOpts) {
  return new Stagehand({
    env: "LOCAL",
    model: modelConfig() as any,
    cacheDir: o.cacheDir,
    verbose: o.verbose ?? 1,
    disablePino: true,
    logger: (l: LogLine) => {
      o.onLog?.(l);
      if ((l.level ?? 1) <= (o.verbose ?? 1)) {
        const cat = l.category ? `[${l.category}] ` : "";
        process.stderr.write(`  · ${cat}${l.message}\n`);
      }
    },
    localBrowserLaunchOptions: {
      headless: o.headless ?? true,
      executablePath: chromePath(),
      viewport: { width: 1280, height: 900 },
    },
  });
}
