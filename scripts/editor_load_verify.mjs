#!/usr/bin/env node

import { spawn } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import net from "node:net";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const NODE_COUNT = 64;
const RENDER_LIMIT_MS = 2_000;
const CONTROL_LIMIT_MS = 250;

function delay(milliseconds) {
  return new Promise((resolveDelay) => setTimeout(resolveDelay, milliseconds));
}

function freePort() {
  return new Promise((resolvePort, reject) => {
    const server = net.createServer();
    server.unref();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      server.close(() => typeof address === "object" && address
        ? resolvePort(address.port)
        : reject(new Error("failed to allocate a loopback port")));
    });
  });
}

async function waitForHttp(url) {
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url, { cache: "no-store" });
      if (response.ok) return;
    } catch {
      // The bounded local service may still be starting.
    }
    await delay(100);
  }
  throw new Error(`timeout waiting for ${url}`);
}

function chromiumPath() {
  const candidates = [process.env.CHROMIUM_BIN, "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome"].filter(Boolean);
  const executable = candidates.find((candidate) => existsSync(candidate));
  if (!executable) throw new Error("Chromium not found; set CHROMIUM_BIN.");
  return executable;
}

function parseArgs() {
  const options = { report: null, screenshot: null };
  const args = process.argv.slice(2);
  for (let index = 0; index < args.length; index += 1) {
    if (args[index] === "--report") options.report = args[++index];
    else if (args[index] === "--screenshot") options.screenshot = args[++index];
    else throw new Error(`unknown argument: ${args[index]}`);
  }
  return options;
}

async function main() {
  const options = parseArgs();
  const port = await freePort();
  const baseUrl = `http://127.0.0.1:${port}`;
  const runtimeTemp = mkdtempSync(resolve(tmpdir(), "kyn-agent-studio-editor-load-"));
  const serverOutput = [];
  const pageErrors = [];
  const failedRequests = [];
  let browser = null;
  const serverEnvironment = { ...process.env };
  delete serverEnvironment.OPENAI_API_KEY;
  const server = spawn(
    process.env.PYTHON_BIN ?? "python3",
    ["-m", "scripts.browser_test_server", "--port", String(port), "--database", resolve(runtimeTemp, "editor-load.sqlite3")],
    { cwd: ROOT, env: serverEnvironment, stdio: ["ignore", "pipe", "pipe"] }
  );
  server.stdout.on("data", (chunk) => serverOutput.push(String(chunk)));
  server.stderr.on("data", (chunk) => serverOutput.push(String(chunk)));

  let report;
  try {
    await waitForHttp(`${baseUrl}/healthz`);
    const browserEnvironment = { ...process.env };
    delete browserEnvironment.OPENAI_API_KEY;
    browser = await chromium.launch({
      executablePath: chromiumPath(),
      headless: true,
      env: browserEnvironment,
      args: ["--no-sandbox", "--disable-gpu", "--disable-extensions", "--disable-background-networking", "--no-first-run"]
    });
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    page.setDefaultTimeout(30_000);
    page.on("pageerror", (error) => pageErrors.push(error.message));
    page.on("console", (message) => {
      const expectedBootstrapMiss = message.type() === "error" && message.text().includes("401");
      if (message.type() === "error" && !expectedBootstrapMiss) pageErrors.push(message.text());
    });
    page.on("requestfailed", (request) => failedRequests.push(`${request.method()} ${request.url()} ${request.failure()?.errorText ?? "failed"}`));

    await page.goto(`${baseUrl}/app/`, { waitUntil: "networkidle" });
    await page.getByRole("button", { name: "Open an isolated Studio" }).click();
    await page.locator(".flow-studio").waitFor({ state: "visible" });

    const created = await page.evaluate(async ({ nodeCount }) => {
      const schema = {
        type: "object",
        properties: { value: { type: "string" } },
        required: ["value"],
        additionalProperties: false
      };
      async function post(path, body) {
        const response = await fetch(path, {
          method: "POST",
          credentials: "same-origin",
          headers: { Accept: "application/json", "Content-Type": "application/json" },
          body: JSON.stringify(body)
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload?.error?.message ?? `${path} HTTP ${response.status}`);
        return payload.data;
      }
      const action = await post("/api/v1/studio/actions", {
        name: "Editor load passthrough",
        slug: "editor-load-passthrough",
        description: "Render the maximum supported graph through one bounded contract.",
        kind: "transform",
        input_schema: schema,
        output_schema: schema,
        config: { operation: "map", mappings: { value: { source: "input", path: "value" } } },
        agent_version_id: null
      });
      const nodes = [];
      const routes = [];
      for (let index = 0; index < nodeCount; index += 1) {
        const nodeId = `node-${String(index + 1).padStart(2, "0")}`;
        const row = Math.floor(index / 8);
        const rawColumn = index % 8;
        const column = row % 2 === 0 ? rawColumn : 7 - rawColumn;
        nodes.push({
          id: nodeId,
          type: "action",
          version_id: action.version.id,
          input_mapping: index === 0
            ? { value: { source: "input", path: "value" } }
            : { value: { source: "step", node_id: `node-${String(index).padStart(2, "0")}`, path: "value" } },
          position: { x: 100 + column * 320, y: 100 + row * 220 },
          settings: { max_attempts: 1, backoff_seconds: 0, retry_on: ["provider_failure"], on_error: "fail" }
        });
        if (index > 0) routes.push({ from: `node-${String(index).padStart(2, "0")}`, to: nodeId, outcome: "success" });
      }
      const flow = await post("/api/v1/studio/flows", {
        name: "Maximum editor graph",
        slug: "maximum-editor-graph",
        description: "All sixty-four supported nodes rendered as one typed acyclic graph.",
        input_schema: schema,
        start_node_id: "node-01",
        nodes,
        routes
      });
      return { flowId: flow.id };
    }, { nodeCount: NODE_COUNT });

    await page.reload({ waitUntil: "networkidle" });
    await page.locator(".flow-studio").waitFor({ state: "visible" });
    const renderStarted = performance.now();
    await page.locator("#flow-select").selectOption(created.flowId);
    await page.waitForFunction((count) => document.querySelectorAll(".react-flow__node").length === count, NODE_COUNT);
    const renderMs = performance.now() - renderStarted;
    const nodes = await page.locator(".react-flow__node").count();
    const edges = await page.locator(".react-flow__edge").count();
    const sourceHandles = await page.locator(".react-flow__handle.source").count();

    for (const label of ["Hide node library", "Hide inspector"]) {
      const control = page.getByRole("button", { name: label });
      if (await control.count()) await control.click();
    }
    const controlStarted = performance.now();
    await page.locator(".react-flow__controls-fitview").click();
    await page.evaluate(() => new Promise((resolveFrame) => requestAnimationFrame(() => requestAnimationFrame(resolveFrame))));
    const controlMs = performance.now() - controlStarted;
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);

    if (options.screenshot) {
      const screenshotPath = resolve(ROOT, options.screenshot);
      mkdirSync(dirname(screenshotPath), { recursive: true });
      await page.screenshot({ path: screenshotPath, fullPage: false });
    }
    const checks = {
      all_nodes_rendered: nodes === NODE_COUNT,
      all_edges_rendered: edges === NODE_COUNT - 1,
      independent_output_handles: sourceHandles >= NODE_COUNT * 2,
      graph_render_within_limit: renderMs < RENDER_LIMIT_MS,
      fit_view_within_limit: controlMs < CONTROL_LIMIT_MS,
      no_document_overflow: overflow === 0,
      no_browser_or_request_errors: pageErrors.length === 0 && failedRequests.length === 0
    };
    const passedCount = Object.values(checks).filter(Boolean).length;
    const passed = passedCount === Object.keys(checks).length;
    report = {
      generated_at: new Date().toISOString(),
      surface: "Kyn.ist Agent Studio maximum-graph Chromium load gate",
      runtime: { chromium: chromiumPath(), viewport: "1440x1000", provider_calls: 0 },
      workload: { nodes, edges, source_handles: sourceHandles },
      measurements: { graph_render_ms: Math.round(renderMs * 1000) / 1000, fit_view_control_ms: Math.round(controlMs * 1000) / 1000, document_overflow_px: overflow },
      thresholds: { graph_render_below_ms: RENDER_LIMIT_MS, fit_view_control_below_ms: CONTROL_LIMIT_MS },
      diagnostics: { page_errors: pageErrors, failed_requests: failedRequests, server_tail: serverOutput.join("").trim().split("\n").slice(-8) },
      checks,
      summary: { checks: Object.keys(checks).length, passed: passedCount, failed: Object.keys(checks).length - passedCount }
    };
    if (!passed) throw new Error(`maximum-graph editor gate failed: ${JSON.stringify(report)}`);
  } finally {
    await browser?.close();
    server.kill("SIGTERM");
    await delay(120);
    rmSync(runtimeTemp, { recursive: true, force: true });
  }

  const encoded = `${JSON.stringify(report, null, 2)}\n`;
  if (options.report) {
    const reportPath = resolve(ROOT, options.report);
    mkdirSync(dirname(reportPath), { recursive: true });
    writeFileSync(reportPath, encoded, "utf8");
  }
  process.stdout.write(encoded);
}

main().catch((error) => {
  process.stderr.write(`${error.stack ?? error.message}\n`);
  process.exitCode = 1;
});
