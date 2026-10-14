// Wiring check (spends about 2 Gemini calls, decides nothing):
//  1. stagehand.extract() on the AP Desk dashboard -> proves model + proxy base URL + key placeholder work.
//  2. a 1-step DOM-mode agent run that only reads the page -> proves Gemini accepts the agent's tool schemas.
// Runs with caching OFF so it leaves nothing in cache/.
//
//   npx tsx check.ts            (or ./run.sh --check)

import { z } from "zod";
import { calls, makeStagehand, MODEL, BASE_URL } from "./lib.ts";

const sh = makeStagehand({ verbose: 0 });
await sh.init();
try {
  const page = sh.context.pages()[0];
  await page.goto("http://localhost:5180/");
  if (!process.argv.includes("--agent-only")) {
    const t0 = Date.now();
    const data = await sh.extract(
    "the main page heading and the labels of the top navigation links",
    z.object({ heading: z.string(), nav: z.array(z.string()) }),
    );
    console.log("extract ->", JSON.stringify(data), `(${calls.n} model call(s), ${Date.now() - t0} ms)`);
  }

  if (!process.argv.includes("--extract-only")) {
    const before = calls.n;
    const agent = sh.agent({ mode: "dom" });
    const r = await agent.execute({
      instruction: "Read the current page and reply with its main heading. Do not click, type or navigate.",
      maxSteps: 1,
    });
    console.log("agent  ->", JSON.stringify({ success: r.success, completed: r.completed, message: r.message, actions: r.actions.map((a) => a.type), usage: r.usage }), `(${calls.n - before} model call(s))`);
  }
  console.log("metrics ->", JSON.stringify(await sh.metrics));
  console.log(`OK: ${MODEL} via ${BASE_URL}; total model calls ${calls.n}`);
} finally {
  await sh.close();
}
