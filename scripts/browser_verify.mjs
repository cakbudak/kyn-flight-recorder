#!/usr/bin/env node

import { spawn } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import net from "node:net";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";
import { APPROVAL_DEMO_BRIEF, shortId } from "../src/lib.js";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const TIMEOUT_MS = Number(process.env.BROWSER_TIMEOUT_MS ?? 180_000);
const checks = [];

function record(name, condition, detail = null) {
  checks.push({ name, status: condition ? "pass" : "fail", detail });
  if (!condition) throw new Error(`check failed: ${name}${detail ? ` (${JSON.stringify(detail)})` : ""}`);
}

function progress(label) {
  process.stderr.write(`[browser] ${label}\n`);
}

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
      const port = typeof address === "object" && address ? address.port : null;
      server.close(() => port === null ? reject(new Error("failed to allocate a loopback port")) : resolvePort(port));
    });
  });
}

async function waitForHttp(url, timeout = TIMEOUT_MS) {
  const deadline = Date.now() + timeout;
  let lastError = null;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url, { cache: "no-store" });
      if (response.ok) return response;
      lastError = new Error(`HTTP ${response.status}`);
    } catch (error) {
      lastError = error;
    }
    await delay(100);
  }
  throw new Error(`timeout waiting for ${url}: ${lastError?.message ?? "no response"}`);
}

function findChromium() {
  const candidates = [
    process.env.CHROMIUM_BIN,
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome"
  ].filter(Boolean);
  const executable = candidates.find((candidate) => existsSync(candidate));
  if (!executable) throw new Error("Chromium not found; set CHROMIUM_BIN.");
  return executable;
}

function parseArgs() {
  const args = process.argv.slice(2);
  const options = { report: null, artifacts: null, baseUrl: null };
  for (let index = 0; index < args.length; index += 1) {
    const argument = args[index];
    if (argument === "--report") options.report = args[++index];
    else if (argument === "--artifacts") options.artifacts = args[++index];
    else if (argument === "--base-url") {
      const parsed = new URL(args[++index]);
      if (!["http:", "https:"].includes(parsed.protocol)) throw new Error("base URL must use HTTP(S)");
      if (parsed.username || parsed.password || parsed.search || parsed.hash || parsed.pathname !== "/") {
        throw new Error("base URL must be an origin without credentials, path, query, or fragment");
      }
      options.baseUrl = parsed.origin;
    } else throw new Error(`unknown argument: ${argument}`);
  }
  return options;
}

async function workspaceSnapshot(page) {
  return page.evaluate(async () => {
    const response = await fetch("/api/v1/workspace", {
      credentials: "same-origin",
      headers: { Accept: "application/json" }
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload?.error?.message ?? `workspace HTTP ${response.status}`);
    return payload.data;
  });
}

async function waitForSnapshot(page, predicate, label, timeout = TIMEOUT_MS) {
  const deadline = Date.now() + timeout;
  let latest = null;
  while (Date.now() < deadline) {
    latest = await workspaceSnapshot(page);
    if (predicate(latest)) return latest;
    await delay(180);
  }
  throw new Error(`timeout waiting for ${label}: ${JSON.stringify(latest)?.slice(0, 500)}`);
}

// A refused mutation leaves the modal open behind an error banner, so every wait
// for the surface that a mutation was supposed to reveal would otherwise expire
// as a bare locator timeout naming the surface rather than the refusal. Racing
// the banner turns "the Runs view never appeared" back into the server's own
// sentence. This only ever fails a journey earlier and with more truth; it never
// admits one, because an unrefused mutation shows no banner at all.
async function awaitSurface(page, selector, label) {
  const surface = page.locator(selector);
  const refusal = page.locator(".error-banner");
  try {
    await surface.waitFor({ state: "visible" });
  } catch (error) {
    // The surface never arrived. A banner standing here names why; a banner left
    // over from an earlier step cannot be the reason, so it is only quoted, never
    // trusted as the diagnosis on its own. Waiting for the surface first rather
    // than racing it keeps a stale banner from failing a journey that succeeded.
    if (await refusal.isVisible()) {
      throw new Error(`${label} did not reveal ${selector}; the surface reported: ${await refusal.innerText()}`);
    }
    throw error;
  }
}

function verifyChain(run) {
  return run.events.every((event, index) =>
    event.sequence === index + 1 &&
    (index === 0 ? event.prev_hash === "0".repeat(64) : event.prev_hash === run.events[index - 1].event_hash)
  );
}

async function capture(page, path) {
  mkdirSync(dirname(path), { recursive: true });
  await page.screenshot({ path, fullPage: false });
}

async function waitIdle(page) {
  await page.waitForFunction(() => {
    const shell = document.querySelector(".app-shell");
    return !shell || shell.getAttribute("aria-busy") !== "true";
  });
  await delay(60);
}

async function clickAndWait(page, locator) {
  await locator.click();
  await waitIdle(page);
}

async function navigate(page, label) {
  await page.locator(".sidebar").getByText(label, { exact: true }).click();
  await waitIdle(page);
}

async function clickCanvasPane(page) {
  const pane = page.locator(".react-flow__pane");
  const box = await pane.boundingBox();
  if (!box) throw new Error("Flow canvas pane is not visible");
  await pane.click({ position: { x: Math.max(8, box.width - 24), y: 24 } });
}

async function auditTextContrast(page) {
  return page.evaluate(() => {
    const parseColor = (value) => {
      const rgb = value.match(/^rgba?\(([^)]+)\)$/i);
      if (rgb) {
        const values = rgb[1].replaceAll(",", " ").replace("/", " ").split(/\s+/).filter(Boolean).map(Number);
        if (values.length >= 3 && values.slice(0, 3).every(Number.isFinite)) {
          return { r: values[0], g: values[1], b: values[2], a: Number.isFinite(values[3]) ? values[3] : 1 };
        }
      }
      const srgb = value.match(/^color\(srgb\s+([^)]+)\)$/i);
      if (srgb) {
        const parts = srgb[1].replace("/", " ").split(/\s+/).filter(Boolean).map(Number);
        if (parts.length >= 3 && parts.slice(0, 3).every(Number.isFinite)) {
          return { r: parts[0] * 255, g: parts[1] * 255, b: parts[2] * 255, a: Number.isFinite(parts[3]) ? parts[3] : 1 };
        }
      }
      return null;
    };
    const over = (foreground, background) => {
      const alpha = foreground.a + background.a * (1 - foreground.a);
      if (!alpha) return { r: 255, g: 255, b: 255, a: 0 };
      return {
        r: (foreground.r * foreground.a + background.r * background.a * (1 - foreground.a)) / alpha,
        g: (foreground.g * foreground.a + background.g * background.a * (1 - foreground.a)) / alpha,
        b: (foreground.b * foreground.a + background.b * background.a * (1 - foreground.a)) / alpha,
        a: alpha
      };
    };
    const backdrop = (element) => {
      const ancestry = [];
      for (let cursor = element; cursor instanceof Element; cursor = cursor.parentElement) ancestry.push(cursor);
      let color = { r: 255, g: 255, b: 255, a: 1 };
      for (const ancestor of ancestry.reverse()) {
        const layer = parseColor(getComputedStyle(ancestor).backgroundColor);
        if (layer) color = over(layer, color);
      }
      return color;
    };
    const channel = (value) => {
      const normalized = value / 255;
      return normalized <= 0.04045 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4;
    };
    const luminance = (color) => 0.2126 * channel(color.r) + 0.7152 * channel(color.g) + 0.0722 * channel(color.b);
    const ratio = (left, right) => {
      const a = luminance(left);
      const b = luminance(right);
      return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
    };
    const failures = [];
    let tested = 0;
    let minimum = Infinity;
    for (const element of document.body.querySelectorAll("*:not(script):not(style):not(svg):not(path)")) {
      if ([...element.childNodes].every((node) => node.nodeType !== Node.TEXT_NODE || !node.textContent.trim())) continue;
      if (element.closest('[aria-hidden="true"]') || element.matches(":disabled") || element.getAttribute("aria-disabled") === "true") continue;
      const rect = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      if (!rect.width || !rect.height || style.display === "none" || style.visibility !== "visible" || Number(style.opacity) === 0) continue;
      const foreground = parseColor(style.color);
      if (!foreground) continue;
      const background = backdrop(element);
      const measured = ratio(over(foreground, background), background);
      const size = Number.parseFloat(style.fontSize);
      const weight = Number.parseInt(style.fontWeight, 10) || (style.fontWeight === "bold" ? 700 : 400);
      const threshold = size >= 24 || (size >= 18.66 && weight >= 700) ? 3 : 4.5;
      tested += 1;
      minimum = Math.min(minimum, measured);
      if (measured + 0.005 < threshold) failures.push({
        tag: element.tagName.toLowerCase(),
        className: String(element.className).slice(0, 90),
        text: element.textContent.trim().replace(/\s+/g, " ").slice(0, 100),
        ratio: Number(measured.toFixed(2)),
        threshold
      });
    }
    return { tested, minimum: Number(minimum.toFixed(2)), failures: failures.slice(0, 20), failureCount: failures.length };
  });
}

async function ensureInspector(page) {
  const showInspector = page.getByRole("button", { name: "Show inspector" });
  if (await showInspector.count()) {
    await showInspector.click();
    await page.locator(".node-inspector").waitFor({ state: "visible" });
  }
}

async function publishResource(page, label) {
  await clickAndWait(page, page.getByRole("button", { name: label, exact: true }));
}

function fieldControl(scope, label, selector = "input, textarea, select") {
  return scope.locator(".field").filter({ hasText: label }).locator(selector).first();
}

async function main() {
  const options = parseArgs();
  const localTarget = options.baseUrl === null;
  const appPort = localTarget ? await freePort() : null;
  const baseUrl = options.baseUrl ?? `http://127.0.0.1:${appPort}`;
  const runtimeTemp = mkdtempSync(resolve(tmpdir(), "kyn-agent-studio-runtime-"));
  const serverOutput = [];
  const pageErrors = [];
  const failedRequests = [];
  const requestedUrls = [];
  const brakeRefusals = [];
  let server = null;
  let browser = null;
  let fatalError = null;

  try {
    if (localTarget) {
      server = spawn(
        process.env.PYTHON_BIN ?? "python3",
        ["-m", "scripts.browser_test_server", "--port", String(appPort), "--database", resolve(runtimeTemp, "browser.sqlite3")],
        { cwd: ROOT, stdio: ["ignore", "pipe", "pipe"] }
      );
      server.stdout.on("data", (chunk) => serverOutput.push(String(chunk)));
      server.stderr.on("data", (chunk) => serverOutput.push(String(chunk)));
    }

    progress(`opening ${baseUrl}`);
    const healthResponse = await waitForHttp(`${baseUrl}/healthz`);
    const health = await healthResponse.json();
    record(
      "runtime exposes SQLite, browser-session BYOK, and official OpenAI SDK transport",
      health.sqlite === "ready" &&
        health.credential_mode === "browser-session-byok" &&
        health.openai_transport === "official-python-sdk",
      health
    );

    const browserEnvironment = { ...process.env };
    delete browserEnvironment.OPENAI_API_KEY;
    browser = await chromium.launch({
      executablePath: findChromium(),
      headless: true,
      env: browserEnvironment,
      args: [
        "--no-sandbox",
        "--disable-gpu",
        "--disable-extensions",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-default-apps",
        "--disable-sync",
        "--metrics-recording-only",
        "--no-first-run"
      ]
    });
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const page = await context.newPage();
    page.setDefaultTimeout(TIMEOUT_MS);
    page.on("pageerror", (error) => pageErrors.push(error.message));
    page.on("console", (message) => {
      const text = message.text();
      const expectedBootstrapMiss = message.type() === "error" && text.includes("401");
      const expectedBrakeRefusal = message.type() === "error" && text.includes("409");
      if (expectedBrakeRefusal) brakeRefusals.push(text);
      if (message.type() === "error" && !expectedBootstrapMiss && !expectedBrakeRefusal) pageErrors.push(text);
    });
    page.on("request", (request) => requestedUrls.push(request.url()));
    page.on("requestfailed", (request) => failedRequests.push(`${request.method()} ${request.url()} ${request.failure()?.errorText ?? "failed"}`));

    await page.goto(`${baseUrl}/app/`, { waitUntil: "networkidle" });
    await page.locator(".onboarding-shell").waitFor({ state: "visible" });
    const onboarding = {
      title: await page.locator(".onboarding-copy h1").innerText(),
      previewNodes: await page.locator(".preview-node").count(),
      overflow: await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    };
    record(
      "onboarding explains a configurable operating system rather than a scripted click demo",
      onboarding.title.includes("Build agent workflows") && onboarding.previewNodes === 4,
      onboarding
    );
    record("desktop onboarding has no document overflow", onboarding.overflow === 0, onboarding.overflow);
    if (options.artifacts) await capture(page, resolve(ROOT, options.artifacts, "01-onboarding.png"));

    await page.getByRole("button", { name: "Open an isolated Studio" }).click();
    await page.locator(".flow-studio").waitFor({ state: "visible" });
    let snapshot = await workspaceSnapshot(page);
    record(
      "fresh workspace seeds editable Actions, Agents, Prompts, Skills, and a nontrivial Flow",
      snapshot.studio.actions.length >= 9 &&
        snapshot.agents.length >= 3 &&
        snapshot.prompts.length >= 3 &&
        snapshot.skills.length >= 3 &&
        snapshot.studio.flows[0].version.nodes.length >= 4,
      {
        actions: snapshot.studio.actions.length,
        agents: snapshot.agents.length,
        prompts: snapshot.prompts.length,
        skills: snapshot.skills.length,
        seeded_nodes: snapshot.studio.flows[0].version.nodes.length
      }
    );

    progress("verifying the full-size graph editor");
    const initialCanvas = {
      nodes: await page.locator(".react-flow__node").count(),
      edges: await page.locator(".react-flow__edge").count(),
      sourceHandles: await page.locator(".kyn-node .source-handle").count(),
      width: await page.locator(".canvas-shell").evaluate((element) => Math.round(element.getBoundingClientRect().width))
    };
    const handleTops = await page.locator(".kyn-node").first().locator(".source-handle").evaluateAll((items) => items.map((item) => Math.round(item.getBoundingClientRect().top)));
    record(
      "published Flow renders as a real editable graph with independent named ports",
      initialCanvas.nodes >= 4 && initialCanvas.edges >= 3 && initialCanvas.sourceHandles >= 8 && new Set(handleTops).size === handleTops.length,
      { ...initialCanvas, first_node_handle_tops: handleTops }
    );
    await page.getByRole("button", { name: "Hide node library" }).click();
    const hideInspector = page.getByRole("button", { name: "Hide inspector" });
    if (await hideInspector.count()) await hideInspector.click();
    await delay(450);
    const expandedWidth = await page.locator(".canvas-shell").evaluate((element) => Math.round(element.getBoundingClientRect().width));
    record("both side panels collapse into a genuinely full canvas", expandedWidth > initialCanvas.width + 200, { initial: initialCanvas.width, expanded: expandedWidth });
    if (options.artifacts) await capture(page, resolve(ROOT, options.artifacts, "02-flow-studio.png"));
    await page.getByRole("button", { name: "Show node library" }).click();
    const showInspector = page.getByRole("button", { name: "Show inspector" });
    if (await showInspector.count()) await showInspector.click();

    progress("editing a seeded Action and creating a multi-output Router");
    await navigate(page, "Actions");
    await page.locator(".registry-item").filter({ hasText: "Quality gate" }).click();
    const actionEditor = page.locator(".registry-editor");
    const descriptionField = fieldControl(actionEditor, "Description", "textarea");
    const priorDescription = await descriptionField.inputValue();
    await descriptionField.fill(`${priorDescription} Browser-verified successor.`);
    await publishResource(page, "Publish successor");
    progress("seeded Action successor submitted");
    snapshot = await waitForSnapshot(page, (value) => value.studio.actions.some((item) => item.slug === "quality-gate" && item.current_version === 2), "Action successor");
    const revisedGate = snapshot.studio.actions.find((item) => item.slug === "quality-gate");
    record(
      "a seeded Action is selectable, editable, and append-versioned without mutation",
      revisedGate.current_version === 2 && revisedGate.versions[1].version === 1 && revisedGate.description.includes("Browser-verified"),
      { versions: revisedGate.versions.map((item) => item.version) }
    );

    progress("opening new Router Action editor");
    await page.getByRole("button", { name: "New Action" }).click();
    await fieldControl(actionEditor, "Name", "input").fill("Browser segment router");
    await actionEditor.getByLabel("Executor kind").selectOption("router");
    await actionEditor.getByRole("tab", { name: "Outputs" }).click();
    const outputLabels = await actionEditor.locator(".outcome-row input:first-child").evaluateAll((items) => items.map((item) => item.value));
    record(
      "Action authoring exposes more than success and failure as first-class outputs",
      outputLabels.length === 4 && ["Priority", "Standard", "Fallback", "Error"].every((label) => outputLabels.includes(label)),
      outputLabels
    );
    if (options.artifacts) await capture(page, resolve(ROOT, options.artifacts, "03-action-outputs.png"));
    await publishResource(page, "Publish v1");
    progress("Router Action submitted");
    snapshot = await waitForSnapshot(page, (value) => value.studio.actions.some((item) => item.slug === "browser-segment-router"), "Router Action");
    const routerAction = snapshot.studio.actions.find((item) => item.slug === "browser-segment-router");
    record("Router Action publishes a typed four-port contract", routerAction.version.kind === "router" && routerAction.version.outcomes.length === 4, routerAction.version.outcomes);

    progress("creating Prompt, Skill, and Agent through their own registries");
    await navigate(page, "Prompts");
    await page.getByRole("button", { name: "New Prompt" }).click();
    await fieldControl(page.locator(".registry-editor"), "Name", "input").fill("Browser risk prompt");
    await publishResource(page, "Publish v1");
    await waitForSnapshot(page, (value) => value.prompts.some((item) => item.slug === "browser-risk-prompt"), "Prompt creation");

    await navigate(page, "Skills");
    await page.getByRole("button", { name: "New Skill" }).click();
    const skillEditor = page.locator(".registry-editor");
    await fieldControl(skillEditor, "Name", "input").fill("Browser review skill");
    await skillEditor.locator(".choice-card").filter({ hasText: "Inspect release policy" }).click();
    await skillEditor.locator(".choice-card").filter({ hasText: "Browser segment router" }).click();
    await publishResource(page, "Publish v1");
    await waitForSnapshot(page, (value) => value.skills.some((item) => item.slug === "browser-review-skill"), "Skill creation");

    await navigate(page, "Agents");
    await page.getByRole("button", { name: "New Agent" }).click();
    const agentEditor = page.locator(".registry-editor");
    await fieldControl(agentEditor, "Name", "input").fill("Browser review agent");
    const promptSelect = agentEditor.getByLabel("Prompt version");
    const browserPromptOption = promptSelect.locator("option").filter({ hasText: "Browser risk prompt" });
    await promptSelect.selectOption(await browserPromptOption.getAttribute("value"));
    await agentEditor.locator(".choice-card").filter({ hasText: "Browser review skill" }).click();
    await publishResource(page, "Publish v1");
    snapshot = await waitForSnapshot(page, (value) => value.agents.some((item) => item.slug === "browser-review-agent"), "Agent creation");
    const browserAgent = snapshot.agents.find((item) => item.slug === "browser-review-agent");
    const browserSkill = snapshot.skills.find((item) => item.slug === "browser-review-skill");
    const browserPrompt = snapshot.prompts.find((item) => item.slug === "browser-risk-prompt");
    record(
      "Agent pins the exact Prompt and Skill versions and inherits only their explicit authority",
      browserAgent.version.prompt_version_id === browserPrompt.version.id &&
        browserAgent.version.skill_version_ids.includes(browserSkill.version.id) &&
        browserSkill.version.allowed_action_version_ids.includes(routerAction.version.id),
      {
        prompt: browserAgent.version.prompt_version_id,
        skills: browserAgent.version.skill_version_ids,
        actions: browserSkill.version.allowed_action_version_ids
      }
    );

    await navigate(page, "Actions");
    await page.locator(".registry-item").filter({ hasText: "AI launch analysis" }).click();
    await page.locator(".registry-editor").getByRole("tab", { name: "Execution" }).click();
    record(
      "AI configuration is visible as an Agent stack rather than hidden node state",
      await page.locator(".registry-editor .stack-card").isVisible() && await page.locator(".registry-editor").getByLabel("Pinned Agent version").isVisible()
    );
    if (options.artifacts) await capture(page, resolve(ROOT, options.artifacts, "04-ai-stack.png"));

    progress("composing, publishing, running, and revising a user-defined Flow");
    await navigate(page, "Flow Studio");
    await page.getByRole("button", { name: "New Flow" }).click();
    await page.locator(".empty-canvas").waitFor({ state: "visible" });
    await page.getByLabel("Search node library").fill("Browser segment router");
    await page.locator(".palette-card").filter({ hasText: "Browser segment router" }).click();
    await page.locator(".react-flow__node").first().waitFor({ state: "visible" });
    record("New Flow starts from an actually empty graph", await page.locator(".react-flow__node").count() === 1, { nodes: await page.locator(".react-flow__node").count() });
    const routerPorts = await page.locator(".react-flow__node .source-handle").count();
    await clickCanvasPane(page);
    const flowInspector = page.locator(".node-inspector");
    await fieldControl(flowInspector, "Name", "input").fill("Browser decision Flow");
    await fieldControl(flowInspector, "Purpose", "textarea").fill("Route typed work through four independently observable outcomes.");
    await clickAndWait(page, page.getByRole("button", { name: "Publish Flow" }));
    snapshot = await waitForSnapshot(page, (value) => value.studio.flows.some((item) => item.slug === "browser-decision-flow"), "Flow v1 publication");
    const decisionFlow = snapshot.studio.flows.find((item) => item.slug === "browser-decision-flow");
    record(
      "visual authoring publishes the graph, positions, mappings, and four outcomes",
      routerPorts === 4 && decisionFlow.version.nodes.length === 1 && decisionFlow.version.outcomes.length === 4 && decisionFlow.version.nodes[0].position !== null,
      { ports: routerPorts, outcomes: decisionFlow.version.outcomes.map((item) => item.id), node: decisionFlow.version.nodes[0] }
    );

    const runLauncher = page.getByRole("button", { name: "Run", exact: true });
    await runLauncher.click();
    const startRunDialog = page.getByRole("dialog", { name: `Run ${decisionFlow.name}` });
    await startRunDialog.waitFor({ state: "visible" });
    const closeDialog = startRunDialog.getByRole("button", { name: "Close dialog" });
    const pinAndStart = startRunDialog.getByRole("button", { name: "Pin and start Run" });
    const focusedOnClose = await closeDialog.evaluate((element) => document.activeElement === element);
    await page.keyboard.press("Shift+Tab");
    const wrappedBackward = await pinAndStart.evaluate((element) => document.activeElement === element);
    await page.keyboard.press("Tab");
    const wrappedForward = await closeDialog.evaluate((element) => document.activeElement === element);
    await page.keyboard.press("Escape");
    await startRunDialog.waitFor({ state: "hidden" });
    const restoredToLauncher = await runLauncher.evaluate((element) => document.activeElement === element);
    record(
      "dialogs receive focus, trap keyboard traversal, close with Escape, and restore the opener",
      focusedOnClose && wrappedBackward && wrappedForward && restoredToLauncher,
      { focusedOnClose, wrappedBackward, wrappedForward, restoredToLauncher }
    );
    await runLauncher.click();
    await fieldControl(page.locator(".modal"), "Run input", "textarea").fill(JSON.stringify({ value: "priority" }, null, 2));
    await page.getByRole("button", { name: "Pin and start Run" }).click();
    await awaitSurface(page, ".runs-page", "starting the Run");
    snapshot = await waitForSnapshot(page, (value) => value.studio.runs.some((run) => run.flow_id === decisionFlow.id && run.status === "completed"), "deterministic Flow completion");
    const decisionRun = snapshot.studio.runs.find((run) => run.flow_id === decisionFlow.id);
    record(
      "deterministic Flow executes without a key and exposes the selected named outcome with evidence",
      decisionRun.status === "completed" && decisionRun.outcome === "priority" && decisionRun.steps[0].route_outcome === "priority" && decisionRun.model_calls.length === 0 && decisionRun.action_receipts.length === 1 && verifyChain(decisionRun),
      { id: decisionRun.id, status: decisionRun.status, outcome: decisionRun.outcome, events: decisionRun.events.length }
    );

    await navigate(page, "Flow Studio");
    await page.locator("#flow-select").selectOption(decisionFlow.id);
    await ensureInspector(page);
    await clickCanvasPane(page);
    await fieldControl(page.locator(".node-inspector"), "Name", "input").fill("Browser decision Flow verified");
    await page.locator(".react-flow__node").click();
    await page.locator(".node-inspector").getByLabel("Max attempts").fill("2");
    await clickAndWait(page, page.getByRole("button", { name: "Publish successor" }));
    snapshot = await waitForSnapshot(page, (value) => value.studio.flows.some((item) => item.id === decisionFlow.id && item.current_version === 2), "Flow successor");
    const revisedDecisionFlow = snapshot.studio.flows.find((item) => item.id === decisionFlow.id);
    const pinnedOldRun = snapshot.studio.runs.find((run) => run.id === decisionRun.id);
    record(
      "Flow editing appends a successor while prior Run graph pins remain unchanged",
      revisedDecisionFlow.name === "Browser decision Flow verified" && revisedDecisionFlow.version.nodes[0].settings.max_attempts === 2 && pinnedOldRun.flow_version === 1 && pinnedOldRun.flow_graph.nodes[0].settings.max_attempts === 1,
      { current: revisedDecisionFlow.current_version, old_run: pinnedOldRun.flow_version }
    );

    progress("reusing a completed Flow as a linked child Run");
    await page.getByRole("button", { name: "New Flow" }).click();
    await page.locator(".empty-canvas").waitFor({ state: "visible" });
    await page.getByRole("tab", { name: /^Flows/ }).click();
    await page.locator(".palette-card").filter({ hasText: "Browser decision Flow verified" }).click();
    await clickCanvasPane(page);
    await fieldControl(page.locator(".node-inspector"), "Name", "input").fill("Reusable browser orchestration");
    await fieldControl(page.locator(".node-inspector"), "Purpose", "textarea").fill("Execute a published Flow as a typed node with separate child evidence.");
    await clickAndWait(page, page.getByRole("button", { name: "Publish Flow" }));
    snapshot = await waitForSnapshot(page, (value) => value.studio.flows.some((item) => item.slug === "reusable-browser-orchestration"), "parent Flow publication");
    const parentFlow = snapshot.studio.flows.find((item) => item.slug === "reusable-browser-orchestration");
    record("published Flows appear in the node library as immutable subflow capabilities", parentFlow.version.nodes[0].type === "flow" && parentFlow.version.nodes[0].version_id === revisedDecisionFlow.version.id, parentFlow.version.nodes[0]);

    await page.getByRole("button", { name: "Run", exact: true }).click();
    await fieldControl(page.locator(".modal"), "Run input", "textarea").fill(JSON.stringify({ value: "standard" }, null, 2));
    await page.getByRole("button", { name: "Pin and start Run" }).click();
    await awaitSurface(page, ".runs-page", "starting the Run");
    snapshot = await waitForSnapshot(page, (value) => value.studio.runs.some((run) => run.flow_id === parentFlow.id && run.status === "completed"), "parent and child Run completion");
    const parentRun = snapshot.studio.runs.find((run) => run.flow_id === parentFlow.id);
    const subflowRun = snapshot.studio.runs.find((run) => run.parent_run_id === parentRun.id && run.relation_kind === "subflow");
    record(
      "subflow execution retains linked parent/child truth and independent hash chains",
      parentRun.outcome === "standard" && subflowRun?.status === "completed" && subflowRun.flow_version === 2 && subflowRun.correlation_id === parentRun.correlation_id && verifyChain(parentRun) && verifyChain(subflowRun),
      { parent: parentRun.id, child: subflowRun?.id, outcome: parentRun.outcome }
    );

    progress("binding and invoking the same Flow through a signed webhook");
    await navigate(page, "Overview");
    await page.getByRole("button", { name: "Add trigger" }).click();
    await fieldControl(page.locator(".modal"), "Flow", "select").selectOption(parentFlow.id);
    await fieldControl(page.locator(".modal"), "Trigger name", "input").fill("Browser verification webhook");
    await clickAndWait(page, page.getByRole("button", { name: "Create binding" }));
    const hookPath = await page.locator(".webhook-reveal code").innerText();
    const hookResult = await page.evaluate(async ({ path, input }) => {
      const response = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input)
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.error?.message ?? `webhook HTTP ${response.status}`);
      return payload.data;
    }, { path: hookPath, input: { value: "priority" } });
    record(
      "signed webhook enters the same pinned Run seam and executes the reusable Flow",
      hookResult.run.status === "completed" && hookResult.run.flow_version === 1 && hookResult.run.outcome === "priority",
      { trigger: hookResult.trigger_id, run: hookResult.run.id, outcome: hookResult.run.outcome }
    );
    await page.locator(".trigger-list article").filter({ hasText: "Browser verification webhook" }).getByRole("button", { name: "Disable" }).click();
    await waitIdle(page);

    progress("configuring browser-owned OpenAI authority");
    await navigate(page, "Settings");
    const browserKey = localTarget ? "test-browser-owned-openai-key-for-playwright" : process.env.OPENAI_API_KEY;
    if (!browserKey) throw new Error("OPENAI_API_KEY is required for deployed browser verification");
    await page.getByLabel("API key").fill(browserKey);
    await clickAndWait(page, page.getByRole("button", { name: "Save in this tab" }));
    record(
      "OpenAI key remains browser-session state and is never rendered back into the document",
      (await page.evaluate(() => sessionStorage.getItem("kyn.openai.api-key.v1")?.length ?? 0)) >= 20 && !(await page.locator("body").innerText()).includes(browserKey),
      { stored_in_session: true }
    );

    progress("executing the pinned Agent stack through OpenAI and a Human gate");
    await navigate(page, "Flow Studio");
    snapshot = await workspaceSnapshot(page);
    const seededFlow = snapshot.studio.flows.find((item) => item.slug === "agent-reviewed-launch");
    await page.locator("#flow-select").selectOption(seededFlow.id);
    await page.getByRole("button", { name: "Run", exact: true }).click();
    await fieldControl(page.locator(".modal"), "Run input", "textarea").fill(JSON.stringify({ brief: APPROVAL_DEMO_BRIEF }, null, 2));
    await page.getByRole("button", { name: "Pin and start Run" }).click();
    await awaitSurface(page, ".runs-page", "starting the Run");
    snapshot = await waitForSnapshot(page, (value) => value.studio.runs.some((run) => run.flow_id === seededFlow.id && run.status === "waiting_approval"), "AI Run approval pause");
    let aiRun = snapshot.studio.runs.find((run) => run.flow_id === seededFlow.id && run.status === "waiting_approval");
    await page.getByRole("button", { name: "Approve and resume" }).waitFor({ state: "visible" });
    record(
      "AI Flow invokes a pinned Agent stack, records model evidence, and pauses before effects",
      aiRun.model_calls.length >= 1 && aiRun.pending_approval !== null && aiRun.effects.length === 0 && aiRun.steps.some((step) => step.status === "waiting_approval") && verifyChain(aiRun),
      { run: aiRun.id, calls: aiRun.model_calls.length, steps: aiRun.steps.map((step) => step.status), effects: aiRun.effects.length }
    );
    const runGraph = {
      pinned_nodes: seededFlow.version.nodes.length,
      rendered_nodes: await page.locator(".run-graph .run-graph-node").count(),
      minimap_nodes: await page.locator(".run-graph .react-flow__minimap-node").count()
    };
    record(
      "Run graph renders every pinned node and propagates node measurements, probed through the MiniMap because it alone re-derives from live measured dimensions while the canvas keeps drawing from static positions",
      runGraph.rendered_nodes === runGraph.pinned_nodes && runGraph.minimap_nodes === runGraph.pinned_nodes,
      runGraph
    );
    if (options.artifacts) await capture(page, resolve(ROOT, options.artifacts, "05-waiting-approval.png"));
    await page.getByRole("button", { name: "Approve and resume" }).click();
    await clickAndWait(page, page.getByRole("button", { name: "Record approval" }));
    snapshot = await waitForSnapshot(page, (value) => value.studio.runs.some((run) => run.id === aiRun.id && run.status === "completed"), "approved AI Run");
    aiRun = snapshot.studio.runs.find((run) => run.id === aiRun.id);
    record(
      "human decision resumes the exact pinned graph into one attributable idempotent effect",
      aiRun.effects.length === 1 && aiRun.approvals[0].decision.approved === true && verifyChain(aiRun),
      { effects: aiRun.effects.length, actor: aiRun.approvals[0].decision.actor }
    );
    if (options.artifacts) await capture(page, resolve(ROOT, options.artifacts, "06-run-evidence.png"));

    progress("carrying immutable SmartRead citations through a parallel BoardRoom");
    await navigate(page, "Context & Memory");
    await clickAndWait(page, page.getByRole("button", { name: "Import source" }));
    const sourceDialog = page.locator(".modal");
    const importConfirmation = sourceDialog.getByRole("button", { name: "Import immutable source" });
    const importConfirmationGeometry = await sourceDialog.evaluate((dialog) => {
      const body = dialog.querySelector(".modal-body");
      const button = dialog.querySelector('.modal-footer button[type="submit"]');
      if (!body || !button) return null;
      const rect = button.getBoundingClientRect();
      const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
      return {
        top: rect.top,
        bottom: rect.bottom,
        viewport_height: window.innerHeight,
        outside_scroll_region: !body.contains(button),
        pointer_reachable: hit === button || button.contains(hit),
      };
    });
    record(
      "Knowledge import keeps its confirmation visible and reachable without scrolling the form",
      await importConfirmation.isVisible() &&
        importConfirmationGeometry?.top >= 0 &&
        importConfirmationGeometry?.bottom <= importConfirmationGeometry?.viewport_height &&
        importConfirmationGeometry?.outside_scroll_region === true &&
        importConfirmationGeometry?.pointer_reachable === true,
      importConfirmationGeometry
    );
    await fieldControl(sourceDialog, "Name", "input").fill("Browser launch evidence");
    await fieldControl(sourceDialog, "Purpose", "textarea").fill("Immutable launch facts for a cited multi-agent decision.");
    await fieldControl(sourceDialog, "Display filename", "input").fill("browser-launch-evidence.md");
    await fieldControl(sourceDialog, "Source content", "textarea").fill([
      "# Browser launch evidence",
      "",
      "## User value",
      "The context-to-decision loop must remain inspectable from source line to final Run.",
      "",
      "## Authority",
      "No parallel participant may approve work or mint an effect.",
      "A human decision is required after independent synthesis.",
      "",
      "## Falsifier",
      "Quorum without visible dissent is not acceptable evidence."
    ].join("\n"));
    await clickAndWait(page, sourceDialog.getByRole("button", { name: "Import immutable source" }));
    snapshot = await waitForSnapshot(
      page,
      (value) => (value.studio.knowledge_sources ?? []).some((source) => source.slug === "browser-launch-evidence"),
      "immutable Knowledge source"
    );
    const knowledgeSource = snapshot.studio.knowledge_sources.find((source) => source.slug === "browser-launch-evidence");
    await page.getByRole("radio", { name: /Focus/ }).check();
    await page.getByLabel("Last line").waitFor({ state: "visible" });
    record(
      "SmartRead focus initializes a legal bounded range for the selected immutable version",
      await page.getByLabel("First line").inputValue() === "1" &&
        await page.getByLabel("Last line").inputValue() === "11" &&
        await page.getByLabel("Last line").getAttribute("max") === "11",
      {
        first: await page.getByLabel("First line").inputValue(),
        last: await page.getByLabel("Last line").inputValue(),
        source_lines: knowledgeSource.version.line_count
      }
    );
    await page.getByRole("radio", { name: /Glance/ }).check();
    await clickAndWait(page, page.getByRole("button", { name: "Read cited context" }));
    await page.locator(".read-result").waitFor({ state: "visible" });
    const citedReadText = await page.locator(".read-result").innerText();
    record(
      "SmartRead returns bounded source text with an immutable version, fingerprint, and exact line citation",
        knowledgeSource.current_version === 1 &&
        knowledgeSource.version.line_count === 11 &&
        citedReadText.includes("browser-launch-evidence.md:L1-L11") &&
        citedReadText.includes(shortId(knowledgeSource.version.fingerprint, 14)) &&
        citedReadText.includes("No parallel participant may approve work"),
      {
        source: knowledgeSource.id,
        version: knowledgeSource.version.id,
        fingerprint: knowledgeSource.version.fingerprint,
        lines: knowledgeSource.version.line_count
      }
    );
    if (options.artifacts) await capture(page, resolve(ROOT, options.artifacts, "19-smartread-citations.png"));

    await clickAndWait(page, page.getByRole("button", { name: "Take context to BoardRoom" }));
    await page.locator(".boardroom-builder").waitFor({ state: "visible" });
    const roomManifest = await page.locator(".factory-manifest").innerText();
    const boardRoomFormValid = await page.locator(".boardroom-builder").evaluate((form) => form.checkValidity());
    record(
      "the BoardRoom factory exposes every Prompt, Skill, Agent, Action, Flow, quorum, and downstream authority choice before publication",
      boardRoomFormValid &&
        roomManifest.includes("4\nPrompts") &&
        roomManifest.includes("4\nSkills") &&
        roomManifest.includes("4\nAgents") &&
        roomManifest.includes("7\nActions") &&
        roomManifest.includes("1\neditable Flow") &&
        await page.locator(".participant-editor").count() === 3 &&
        await page.getByLabel("Quorum").inputValue() === "2" &&
        await page.getByRole("radio", { name: /Require human decision/ }).isChecked(),
      { manifest: roomManifest.replaceAll("\n", " · "), default_form_valid: boardRoomFormValid }
    );
    if (options.artifacts) await capture(page, resolve(ROOT, options.artifacts, "20-boardroom-factory.png"));
    await clickAndWait(page, page.locator(".builder-publish"));
    snapshot = await waitForSnapshot(page, (value) => (value.studio.boardrooms ?? []).some((room) => room.slug === "launch-decision-room"), "BoardRoom publication");
    const boardRoom = snapshot.studio.boardrooms.find((room) => room.slug === "launch-decision-room");
    const boardRoomDialog = page.locator(".modal");
    await boardRoomDialog.waitFor({ state: "visible" });
    const transferredContext = await fieldControl(boardRoomDialog, "Cited context", "textarea").inputValue();
    record(
      "SmartRead transfers the cited envelope into the real BoardRoom Run contract without flattening provenance",
      transferredContext.includes("browser-launch-evidence.md:L1-L11") &&
        transferredContext.includes(knowledgeSource.version.fingerprint.slice(0, 12)) &&
        boardRoom.model_call_forecast === 4 &&
        boardRoom.editable_in_flow_studio === true,
      { flow: boardRoom.flow_id, forecast_calls: boardRoom.model_call_forecast }
    );
    await clickAndWait(page, boardRoomDialog.getByRole("button", { name: "Pin and start deliberation" }));
    await awaitSurface(page, ".runs-page", "starting the BoardRoom");
    snapshot = await waitForSnapshot(
      page,
      (value) => value.studio.runs.some((run) => run.flow_id === boardRoom.flow_id && run.status === "waiting_approval"),
      "BoardRoom human gate"
    );
    let boardRoomRun = snapshot.studio.runs.find((run) => run.flow_id === boardRoom.flow_id && run.status === "waiting_approval");
    const fanOutStep = boardRoomRun.steps.find((step) => step.node_type === "fan_out" && step.member_id === null);
    const participantSteps = boardRoomRun.steps.filter((step) => step.parent_step_id === fanOutStep?.id);
    const barrier = fanOutStep?.output?.barrier;
    const participantIds = participantSteps.map((step) => step.member_id);
    const dissentingMemberIds = barrier?.dissenting_members ?? [];
    record(
      "three independent model Steps join through a code-owned barrier while the actual verdict distribution remains first-class evidence",
      boardRoomRun.model_calls.length === 4 &&
        participantSteps.length === 3 &&
        new Set(participantIds).size === 3 &&
        participantSteps.every((step) => step.status === "completed") &&
        barrier?.completed === 3 &&
        barrier?.expected === 3 &&
        barrier?.failed === 0 &&
        barrier?.affirmative + dissentingMemberIds.length === 3 &&
        dissentingMemberIds.every((memberId) => participantIds.includes(memberId)) &&
        barrier?.converged === (barrier?.affirmative >= barrier?.quorum) &&
        boardRoomRun.effects.length === 0 &&
        boardRoomRun.pending_approval !== null &&
        verifyChain(boardRoomRun),
      {
        run: boardRoomRun.id,
        model_calls: boardRoomRun.model_calls.length,
        member_steps: participantIds,
        barrier,
        effects_before_approval: boardRoomRun.effects.length
      }
    );
    await page.getByRole("button", { name: "Approve and resume" }).waitFor({ state: "visible" });
    await page.locator(".parallel-evidence").waitFor({ state: "visible" });
    await page.waitForFunction(({ converged, dissent, memberCount }) => {
      const text = document.querySelector(".parallel-evidence")?.textContent ?? "";
      const routeVisible = text.includes(converged ? "Quorum reached" : "Review route");
      const dissentVisible = dissent.length
        ? text.includes(`Dissent: ${dissent.join(", ")}`)
        : text.includes("No recorded dissent");
      return routeVisible && dissentVisible && text.includes(`${memberCount} separately persisted member Steps`);
    }, { converged: barrier.converged, dissent: dissentingMemberIds, memberCount: participantSteps.length });
    const parallelEvidenceText = await page.locator(".parallel-evidence").innerText();
    const parallelEvidenceUi = {
      members: await page.locator(".parallel-member-grid > article").count(),
      dissentCards: await page.locator(".parallel-member-grid > article.is-dissent").count(),
      authorityNotes: await page.locator(".parallel-evidence footer").getByText("members cannot pause or mint effects", { exact: true }).count(),
      metrics: await page.locator(".parallel-proof-metrics article").evaluateAll((items) => Object.fromEntries(items.map((item) => [item.querySelector("span")?.textContent?.trim().toLowerCase(), item.querySelector("strong")?.textContent?.trim()])))
    };
    const parallelEvidenceContrast = await auditTextContrast(page);
    record(
      "the Runs console makes the fan-out, member verdicts, deterministic join, and surviving dissent visible without opening raw JSON",
      parallelEvidenceUi.members === 3 &&
        parallelEvidenceUi.dissentCards === dissentingMemberIds.length &&
        parallelEvidenceUi.metrics.completed === "3/3" &&
        parallelEvidenceUi.metrics.affirmative === String(barrier.affirmative) &&
        parallelEvidenceUi.metrics.dissenting === String(dissentingMemberIds.length) &&
        parallelEvidenceUi.metrics.failed === "0" &&
        parallelEvidenceUi.authorityNotes === 1 &&
        parallelEvidenceContrast.failureCount === 0,
      {
        characters: parallelEvidenceText.length,
        contrast_samples: parallelEvidenceContrast.tested,
        minimum_contrast: parallelEvidenceContrast.minimum,
        contrast_failures: parallelEvidenceContrast.failures,
        ...parallelEvidenceUi
      }
    );
    await page.locator(".parallel-evidence").scrollIntoViewIfNeeded();
    if (options.artifacts) await capture(page, resolve(ROOT, options.artifacts, "21-boardroom-evidence.png"));
    await page.getByRole("button", { name: "Approve and resume" }).click();
    await clickAndWait(page, page.getByRole("button", { name: "Record approval" }));
    snapshot = await waitForSnapshot(page, (value) => value.studio.runs.some((run) => run.id === boardRoomRun.id && run.status === "completed"), "approved BoardRoom");
    boardRoomRun = snapshot.studio.runs.find((run) => run.id === boardRoomRun.id);
    record(
      "the human gate resumes the exact pinned BoardRoom into an explicit result without granting hidden write authority",
      boardRoomRun.output?.status === "approved" &&
        Array.isArray(boardRoomRun.output?.dissent) &&
        boardRoomRun.approvals[0]?.decision?.approved === true &&
        boardRoomRun.effects.length === 0 &&
        verifyChain(boardRoomRun),
      { status: boardRoomRun.output?.status, dissent: boardRoomRun.output?.dissent, effects: boardRoomRun.effects.length }
    );

    await navigate(page, "BoardRooms");
    await clickAndWait(page, page.getByRole("button", { name: "Edit exact Flow" }));
    await awaitSurface(page, ".flow-studio", "opening the generated BoardRoom Flow");
    await page.locator(".react-flow__node").filter({ hasText: "Parallel fan-out" }).click();
    await page.locator(".fan-out-inspector").waitFor({ state: "visible" });
    const memberIdInputs = page.locator('.fanout-member-editor input[disabled]');
    const memberIdValues = await memberIdInputs.evaluateAll((items) => items.map((item) => item.value));
    await page.getByLabel("Affirmative votes").fill("3");
    if (await page.locator(".toast").count()) await page.locator(".toast").waitFor({ state: "hidden", timeout: 6000 });
    if (options.artifacts) await capture(page, resolve(ROOT, options.artifacts, "23-boardroom-flow-editor.png"));
    await clickAndWait(page, page.getByRole("button", { name: "Publish successor" }));
    snapshot = await waitForSnapshot(
      page,
      (value) => value.studio.flows.some((flow) => flow.id === boardRoom.flow_id && flow.current_version === 2),
      "editable BoardRoom successor"
    );
    const revisedBoardRoomFlow = snapshot.studio.flows.find((flow) => flow.id === boardRoom.flow_id);
    const revisedFanOut = revisedBoardRoomFlow.version.nodes.find((node) => node.type === "fan_out");
    const pinnedBoardRoomRun = snapshot.studio.runs.find((run) => run.id === boardRoomRun.id);
    record(
      "a BoardRoom opens as the ordinary full graph, edits its barrier policy, and publishes forward without rewriting prior Run truth or schema-key member IDs",
      revisedBoardRoomFlow.current_version === 2 &&
        revisedFanOut?.barrier.quorum === 3 &&
        memberIdValues.join(",") === "product,risk,operations" &&
        revisedFanOut?.members.map((member) => member.id).join(",") === "product,risk,operations" &&
        pinnedBoardRoomRun.flow_version === 1 &&
        pinnedBoardRoomRun.steps.some((step) => step.member_id === "risk") &&
        barrier.quorum === 2,
      {
        current_flow_version: revisedBoardRoomFlow.current_version,
        current_quorum: revisedFanOut?.barrier.quorum,
        current_members: revisedFanOut?.members.map((member) => member.id),
        pinned_run_version: pinnedBoardRoomRun.flow_version,
        pinned_quorum: barrier.quorum,
        pinned_member_ids: pinnedBoardRoomRun.steps.map((step) => step.member_id).filter(Boolean)
      }
    );

    progress("promoting cited Run evidence through governed Memory");
    await navigate(page, "Context & Memory");
    await page.getByRole("tab", { name: /^Governed Memory/ }).click();
    const memoryForm = page.locator(".memory-candidate-form");
    await memoryForm.waitFor({ state: "visible" });
    await fieldControl(memoryForm, "Completed source Run", "select").selectOption(boardRoomRun.id);
    await delay(100);
    const dissentSummary = dissentingMemberIds.length ? dissentingMemberIds.join(", ") : "none";
    await fieldControl(memoryForm, "Memory title", "input").fill("Parallel verdicts remain inspectable");
    await fieldControl(memoryForm, "Content", "textarea").fill(`The BoardRoom persisted ${barrier.completed} independent member Steps; its code-owned barrier recorded ${barrier.affirmative} affirmative and ${dissentingMemberIds.length} non-affirmative verdicts. Dissenting members: ${dissentSummary}.`);
    await fieldControl(memoryForm, "Why these events prove it", "textarea").fill(`The cited Run ledger contains the parent fan-out Step, ${barrier.completed} child Steps, and the barrier record for this exact verdict distribution.`);
    await clickAndWait(page, memoryForm.getByRole("button", { name: "Create quarantined candidate" }));
    snapshot = await waitForSnapshot(
      page,
      (value) => (value.studio.memory_candidates ?? []).some((candidate) => candidate.source_run_id === boardRoomRun.id),
      "quarantined Memory candidate"
    );
    let memoryCandidate = snapshot.studio.memory_candidates.find((candidate) => candidate.source_run_id === boardRoomRun.id);
    record(
      "Run-derived learning enters append-only quarantine and remains absent from recall",
      memoryCandidate.decision === null &&
        memoryCandidate.qualification === null &&
        memoryCandidate.evidence_event_ids.length >= 1 &&
        !(snapshot.studio.memories ?? []).some((memory) => memory.version?.source_candidate_id === memoryCandidate.id),
      { candidate: memoryCandidate.id, citations: memoryCandidate.evidence_event_ids.length, fingerprint: memoryCandidate.fingerprint }
    );
    await clickAndWait(page, page.getByRole("button", { name: "Run deterministic qualification" }));
    snapshot = await waitForSnapshot(
      page,
      (value) => (value.studio.memory_candidates ?? []).some((candidate) => candidate.id === memoryCandidate.id && candidate.qualification?.passed === true),
      "qualified Memory candidate"
    );
    memoryCandidate = snapshot.studio.memory_candidates.find((candidate) => candidate.id === memoryCandidate.id);
    await fieldControl(page.locator(".memory-decision"), "Memory slug", "input").fill("parallel-verdicts-remain-inspectable");
    await page.getByRole("checkbox", { name: "I reviewed this exact fingerprint" }).check();
    await clickAndWait(page, page.getByRole("button", { name: "Promote to Memory" }));
    snapshot = await waitForSnapshot(
      page,
      (value) => (value.studio.memory_candidates ?? []).some((candidate) => candidate.id === memoryCandidate.id && candidate.decision?.decision === "promoted"),
      "promoted Memory"
    );
    memoryCandidate = snapshot.studio.memory_candidates.find((candidate) => candidate.id === memoryCandidate.id);
    const promotedMemory = (snapshot.studio.memories ?? []).find((memory) => memory.slug === "parallel-verdicts-remain-inspectable");
    await fieldControl(page.locator(".memory-recall-panel"), "Recall terms", "input").fill("parallel verdicts inspectable");
    await clickAndWait(page, page.locator(".memory-recall-panel").getByRole("button", { name: "Recall Memory" }));
    await page.locator(".memory-result-list article").waitFor({ state: "visible" });
    const recalledText = await page.locator(".memory-result-list").innerText();
    record(
      "only a qualified, fingerprint-acknowledged human promotion becomes recallable with full provenance",
      memoryCandidate.qualification?.passed === true &&
        memoryCandidate.decision?.candidate_fingerprint === memoryCandidate.fingerprint &&
        promotedMemory?.state === "active" &&
        promotedMemory.version.source_candidate_id === memoryCandidate.id &&
        recalledText.includes("Parallel verdicts remain inspectable") &&
        recalledText.includes(shortId(boardRoomRun.id, 14)),
      {
        memory: promotedMemory?.id,
        state: promotedMemory?.state,
        source_candidate: promotedMemory?.version?.source_candidate_id,
        source_run: boardRoomRun.id
      }
    );
    if (options.artifacts) await capture(page, resolve(ROOT, options.artifacts, "22-memory-recall.png"));

    progress("composing cited Knowledge and promoted Memory into one editable Flow");
    await page.getByRole("tab", { name: /^SmartRead/ }).click();
    await page.getByRole("radio", { name: /Focus/ }).check();
    await page.getByLabel("First line").fill("1");
    await page.getByLabel("Last line").fill("11");
    await clickAndWait(page, page.getByRole("button", { name: "Read cited context" }));
    await clickAndWait(page, page.getByRole("button", { name: "Compose cited Flow" }));
    const contextFlowDialog = page.getByRole("dialog", { name: "Compose this evidence in Flow Studio" });
    await contextFlowDialog.waitFor({ state: "visible" });
    await fieldControl(contextFlowDialog, "Flow name", "input").fill("Browser cited council Flow");
    await fieldControl(contextFlowDialog, "Governed Memory recall terms", "input").fill("parallel verdicts inspectable");
    const compositionPreview = await contextFlowDialog.locator(".context-flow-preview").innerText();
    record(
      "SmartRead offers a visible closed-loop composition instead of a one-off context handoff",
      compositionPreview.includes("SmartRead") &&
        compositionPreview.includes("Recall") &&
        compositionPreview.includes("Handoff") &&
        compositionPreview.includes("BoardRoom") &&
        await contextFlowDialog.locator(".inline-warning").count() === 0,
      { preview: compositionPreview.replaceAll("\n", " · ") }
    );
    await clickAndWait(page, contextFlowDialog.getByRole("button", { name: "Open editable Flow draft" }));
    await awaitSurface(page, ".flow-studio", "opening the cited Flow draft");
    const smartReadDraftNode = page.locator(".react-flow__node").filter({ hasText: "SmartRead focus" });
    await smartReadDraftNode.waitFor({ state: "visible" });
    await delay(720);
    const draftGeometry = await page.evaluate(() => {
      const canvas = document.querySelector(".canvas-shell")?.getBoundingClientRect();
      const nodes = [...document.querySelectorAll(".react-flow__node")].map((node) => {
        const rect = node.getBoundingClientRect();
        return { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom };
      });
      return {
        canvas: canvas ? { left: canvas.left, top: canvas.top, right: canvas.right, bottom: canvas.bottom } : null,
        nodes,
        all_nodes_visible: Boolean(canvas) && nodes.every((node) =>
          node.left >= canvas.left && node.top >= canvas.top &&
          node.right <= canvas.right && node.bottom <= canvas.bottom
        )
      };
    });
    const citedDraft = {
      nodes: await page.locator(".react-flow__node").count(),
      routes: await page.locator(".react-flow__edge").count(),
      labels: await page.locator(".react-flow__node").evaluateAll((items) => items.map((item) => item.textContent)),
      allNodesVisible: draftGeometry.all_nodes_visible
    };
    if (options.artifacts) await capture(page, resolve(ROOT, options.artifacts, "24-context-flow-draft.png"));
    await smartReadDraftNode.click();
    await page.locator(".node-inspector").waitFor({ state: "visible" });
    citedDraft.sourceVersion = await page.getByLabel("Literal for source_version_id").inputValue();
    citedDraft.firstLine = await page.getByLabel("Literal for line_start").inputValue();
    citedDraft.lastLine = await page.getByLabel("Literal for line_end").inputValue();
    record(
      "the generated Flow draft opens as a complete canvas and exposes source version, read window, Memory recall, cited handoff, and nested council as ordinary editable nodes",
      citedDraft.nodes === 4 &&
        citedDraft.routes === 3 &&
        citedDraft.allNodesVisible === true &&
        citedDraft.labels.some((label) => label.includes("SmartRead focus")) &&
        citedDraft.labels.some((label) => label.includes("Governed Memory recall")) &&
        citedDraft.labels.some((label) => label.includes("Cited context handoff")) &&
        citedDraft.labels.some((label) => label.includes(boardRoom.name)) &&
        citedDraft.sourceVersion === knowledgeSource.version.id &&
        citedDraft.firstLine === "1" &&
        citedDraft.lastLine === "11",
      citedDraft
    );
    await clickAndWait(page, page.getByRole("button", { name: "Publish Flow" }));
    snapshot = await waitForSnapshot(
      page,
      (value) => value.studio.flows.some((flow) => flow.slug === "browser-cited-council-flow"),
      "cited council Flow publication"
    );
    const citedFlow = snapshot.studio.flows.find((flow) => flow.slug === "browser-cited-council-flow");
    const citedNodes = Object.fromEntries(citedFlow.version.nodes.map((node) => [node.id, node]));
    record(
      "the published graph pins the exact source read and maps cited source plus promoted Memory into the nested BoardRoom",
      citedNodes["read-evidence"].input_mapping.source_version_id.value === knowledgeSource.version.id &&
        citedNodes["recall-memory"].input_mapping.query.value === "parallel verdicts inspectable" &&
        citedNodes["handoff-context"].input_mapping.knowledge_context.path === "context" &&
        citedNodes["handoff-context"].input_mapping.memory_context.path === "context" &&
        citedNodes["governed-council"].type === "flow" &&
        citedNodes["governed-council"].input_mapping.context.node_id === "handoff-context",
      { flow: citedFlow.id, version: citedFlow.version.id, nodes: citedFlow.version.nodes }
    );
    await page.getByRole("button", { name: "Run", exact: true }).click();
    await fieldControl(page.locator(".modal"), "Run input", "textarea").fill(JSON.stringify({ brief: "Decide the bounded launch from the automatically supplied cited context." }, null, 2));
    await page.getByRole("button", { name: "Pin and start Run" }).click();
    await awaitSurface(page, ".runs-page", "starting the cited council Flow");
    snapshot = await waitForSnapshot(
      page,
      (value) => value.studio.runs.some((run) => run.flow_id === citedFlow.id && ["waiting_approval", "completed", "failed", "blocked", "cancelled"].includes(run.status)),
      "nested cited council Human gate",
      30_000
    );
    let citedRun = snapshot.studio.runs.find((run) => run.flow_id === citedFlow.id);
    if (citedRun.status !== "waiting_approval") {
      throw new Error(`cited council Flow ended before Human approval: ${JSON.stringify({ status: citedRun.status, error_code: citedRun.error_code, error_message: citedRun.error_message, steps: citedRun.steps.map((step) => ({ node: step.node_id, status: step.status, error_code: step.error_code, error_message: step.error_message })) })}`);
    }
    const citedReadStep = citedRun.steps.find((step) => step.node_id === "read-evidence");
    const citedRecallStep = citedRun.steps.find((step) => step.node_id === "recall-memory");
    const citedHandoffStep = citedRun.steps.find((step) => step.node_id === "handoff-context");
    const citedChild = snapshot.studio.runs.find((run) => run.parent_run_id === citedRun.id && run.relation_kind === "subflow");
    record(
      "one real Run automatically carries immutable source citations and active Human-promoted Memory into the nested BoardRoom",
      citedReadStep?.output?.context.includes("browser-launch-evidence.md:L1-L11") &&
        citedRecallStep?.output?.context.includes("Parallel verdicts remain inspectable") &&
        citedRecallStep?.output?.context.includes(boardRoomRun.id) &&
        citedHandoffStep?.output?.text.includes("CURRENT IMMUTABLE SOURCE EVIDENCE") &&
        citedHandoffStep?.output?.text.includes("ACTIVE HUMAN-PROMOTED MEMORY") &&
        citedChild?.status === "waiting_approval" &&
        citedRun.ledger_verified === true,
      { parent: citedRun.id, child: citedChild?.id, read_chars: citedReadStep?.output?.context.length, recalled_chars: citedRecallStep?.output?.context.length }
    );
    await page.locator(".run-list-item").filter({ hasText: shortId(citedChild.id) }).first().click();
    await page.getByRole("button", { name: "Approve and resume" }).click();
    await clickAndWait(page, page.getByRole("button", { name: "Record approval" }));
    snapshot = await waitForSnapshot(
      page,
      (value) => value.studio.runs.some((run) => run.id === citedRun.id && run.status === "completed"),
      "completed cited council parent Run"
    );
    citedRun = snapshot.studio.runs.find((run) => run.id === citedRun.id);
    record(
      "the nested Human decision resumes the same outer Flow without bypassing the BoardRoom authority boundary",
      citedRun.status === "completed" && citedRun.output?.status === "approved" && verifyChain(citedRun),
      { run: citedRun.id, status: citedRun.status, outcome: citedRun.outcome }
    );

    progress("distilling, qualifying, and human-promoting an evidence-bound capability");
    const agentVersionsBeforeForge = snapshot.agents.map((agent) => `${agent.id}:${agent.current_version}`).sort();
    const skillsBeforeForge = snapshot.skills.length;
    const sourceModelCall = aiRun.model_calls[0];
    await navigate(page, "Capability Forge");
    await clickAndWait(page, page.getByRole("button", { name: "Distil candidate" }));
    const forgeDialog = page.locator(".modal");
    await fieldControl(forgeDialog, "Completed source model Step", "select").selectOption(`${aiRun.id}:${sourceModelCall.id}`);
    const distillerSelect = fieldControl(forgeDialog, "Independent distiller Agent", "select");
    const distillerAgentVersionId = await distillerSelect.inputValue();
    await clickAndWait(page, forgeDialog.getByRole("button", { name: "Distil into quarantine" }));
    snapshot = await waitForSnapshot(
      page,
      (value) => (value.studio.skill_candidates ?? []).some((candidate) => candidate.source.run_id === aiRun.id),
      "quarantined Skill candidate"
    );
    let forged = snapshot.studio.skill_candidates.find((candidate) => candidate.source.run_id === aiRun.id);
    await page.locator(".candidate-detail").waitFor({ state: "visible" });
    const quarantineText = await page.locator(".forge-page").innerText();
    record(
      "Capability Forge distils one completed model Step through a different logical Agent into an authority-free quarantine",
      forged.status === "quarantined" &&
        forged.source.model_call_id === sourceModelCall.id &&
        forged.source.agent_id !== forged.distillation.agent_id &&
        forged.source.agent_version_id !== forged.distillation.agent_version_id &&
        forged.distillation.agent_version_id === distillerAgentVersionId &&
        forged.distillation.status === "completed" &&
        forged.evidence_event_ids.length >= 1 &&
        forged.authority.allowed_tools.length === 0 &&
        forged.authority.allowed_action_version_ids.length === 0 &&
        quarantineText.includes("Authority delta = 0") &&
        quarantineText.includes("Qualification is not a performance claim"),
      {
        candidate: forged.id,
        source_run: forged.source.run_id,
        source_agent: forged.source.agent_id,
        source_agent_version: forged.source.agent_version_id,
        distiller_agent: forged.distillation.agent_id,
        distiller_agent_version: forged.distillation.agent_version_id,
        cited_events: forged.evidence_event_ids.length,
        authority_delta: 0
      }
    );
    if (options.artifacts) await capture(page, resolve(ROOT, options.artifacts, "17-forge-quarantine.png"));

    await clickAndWait(page, page.getByRole("button", { name: "Qualify candidate" }));
    snapshot = await waitForSnapshot(
      page,
      (value) => (value.studio.skill_candidates ?? []).some((candidate) => candidate.id === forged.id && candidate.status === "qualified"),
      "qualified Skill candidate"
    );
    forged = snapshot.studio.skill_candidates.find((candidate) => candidate.id === forged.id);
    const qualificationText = await page.locator(".qualification-panel").innerText();
    record(
      "the provenance gate replays source truth, citations, independence, fingerprints, and zero authority without another model call",
      forged.qualification?.passed === true &&
        forged.qualification.checks.length === 8 &&
        forged.qualification.checks.every((check) => check.passed) &&
        qualificationText.includes("8/8 gates") &&
        qualificationText.includes("Ledger Chain") &&
        qualificationText.includes("Independent Distiller") &&
        qualificationText.includes("Zero Authority Delta"),
      { checks: forged.qualification?.checks.map((check) => check.id), passed: forged.qualification?.passed }
    );

    await clickAndWait(page, page.getByRole("button", { name: "Review promotion" }));
    const promotionDialog = page.locator(".modal");
    await promotionDialog.getByRole("checkbox").check();
    await clickAndWait(page, promotionDialog.getByRole("button", { name: "Publish Skill v1" }));
    snapshot = await waitForSnapshot(
      page,
      (value) => (value.studio.skill_candidates ?? []).some((candidate) => candidate.id === forged.id && candidate.status === "promoted"),
      "promoted Skill candidate"
    );
    forged = snapshot.studio.skill_candidates.find((candidate) => candidate.id === forged.id);
    const promotedSkill = snapshot.skills.find((skill) => skill.id === forged.promoted_skill?.skill_id);
    const agentVersionsAfterForge = snapshot.agents.map((agent) => `${agent.id}:${agent.current_version}`).sort();
    record(
      "human promotion publishes one immutable Skill v1 while every Agent and Flow remains pinned until a later successor",
      forged.status === "promoted" &&
        forged.decision?.acknowledged === true &&
        promotedSkill?.current_version === 1 &&
        promotedSkill.version.instructions === forged.instructions &&
        promotedSkill.version.allowed_tools.length === 0 &&
        promotedSkill.version.allowed_action_version_ids.length === 0 &&
        snapshot.skills.length === skillsBeforeForge + 1 &&
        JSON.stringify(agentVersionsAfterForge) === JSON.stringify(agentVersionsBeforeForge) &&
        (await page.locator(".candidate-decision.is-promoted").innerText()).includes("Published as immutable Skill v1"),
      {
        skill: promotedSkill?.id,
        version: promotedSkill?.current_version,
        agents_changed: false,
        authority_delta: 0
      }
    );
    if (options.artifacts) await capture(page, resolve(ROOT, options.artifacts, "18-forge-promoted.png"));

    progress("sweeping one pinned Flow version across brains, then across a brain the provider silently renames");
    // Two sweeps on the same pinned version. The first is what a controlled
    // comparison looks like; the second asks for a model the seam answers under
    // a different name, which is the one live provider behaviour that destroys a
    // comparison while leaving every other field looking healthy. The surface
    // has to make the second unmistakable, and the check is that it does.
    const runComparison = async (models) => {
      await navigate(page, "Comparisons");
      await clickAndWait(page, page.getByRole("button", { name: "New comparison" }));
      const dialog = page.locator(".modal");
      await fieldControl(dialog, "Model-backed Flow", "select").selectOption(seededFlow.id);
      await fieldControl(dialog, "Comparison input", "textarea").fill(JSON.stringify({ brief: APPROVAL_DEMO_BRIEF }, null, 2));
      for (const model of ["gpt-5.6", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"]) {
        const box = dialog.getByRole("checkbox", { name: model, exact: true });
        if ((await box.isChecked()) !== models.includes(model)) await box.click();
      }
      const forecast = (await dialog.locator(".comparison-forecast").innerText()).trim();
      await clickAndWait(page, dialog.getByRole("button", { name: `Spend ${models.length} Runs and compare` }));
      const latest = await waitForSnapshot(
        page,
        // Compare the SET, not the click order: the dialog appends newly ticked
        // models, so the recorded order follows how the boxes were toggled.
        (value) => (value.studio.comparisons ?? []).some((item) => [...item.models].sort().join(",") === [...models].sort().join(",")),
        `comparison of ${models.join(" vs ")}`
      );
      return { forecast, comparison: latest.studio.comparisons.find((item) => [...item.models].sort().join(",") === [...models].sort().join(",")) };
    };

    // The clean pair must avoid whichever model is aliased in THIS target: the
// scripted seam renames gpt-5.6-terra, and the live provider renames
// gpt-5.6 to gpt-5.6-sol. Comparing an aliased model would be correctly
// refused, which is the next check's job, not this one's.
    const cleanPair = localTarget ? ["gpt-5.6", "gpt-5.6-luna"] : ["gpt-5.6-sol", "gpt-5.6-luna"];
    const controlled = await runComparison(cleanPair);
    const scoreboard = page.locator(".scoreboard");
    await scoreboard.waitFor({ state: "visible" });
    const controlledPanel = {
      text: await scoreboard.innerText(),
      verdicts: await page.locator(".comparison-verdict.is-usable").count(),
      alerts: await page.locator(".comparison-verdict[role='alert']").count(),
      enforced: await page.locator(".control-column.is-enforced li").count(),
      uncontrolled: await page.locator(".control-column.is-uncontrolled li").count(),
      rows: await page.locator(".sibling-table tbody tr").count(),
      compromisedRows: await page.locator(".sibling-table tbody tr.is-compromised").count()
    };
    if (options.artifacts) await capture(page, resolve(ROOT, options.artifacts, "13-comparison-controlled.png"));

    const aliased = await runComparison(["gpt-5.6", "gpt-5.6-terra"]);
    const aliasProblem = aliased.comparison.integrity_problems.find((problem) => problem.code === "response_model_mismatch");
    await page.locator(".comparison-verdict.is-unusable").waitFor({ state: "visible" });
    const aliasedPanel = {
      text: await page.locator(".scoreboard").innerText(),
      alerts: await page.locator(".comparison-verdict[role='alert']").count(),
      usableVerdicts: await page.locator(".comparison-verdict.is-usable").count(),
      problems: await page.locator(".integrity-list > li").count(),
      compromisedRows: await page.locator(".sibling-table tbody tr.is-compromised").count(),
      listBadge: (await page.locator(".comparison-list-item.is-active .badge").innerText()).trim()
    };
    if (options.artifacts) await capture(page, resolve(ROOT, options.artifacts, "14-comparison-unusable.png"));

    const siblingRuns = (await workspaceSnapshot(page)).studio.runs.filter((run) => run.comparison_id);
    record(
      "a cross-model sweep renders its proof of control before any measurement, and a sweep whose provider silently renamed a model is marked unusable rather than presentable",
      // The control itself: every sibling of both sweeps pinned one immutable
      // Flow version, so the only recorded delta was the model.
      controlled.comparison.usable === true &&
        new Set(controlled.comparison.siblings.map((sibling) => sibling.flow_version_id)).size === 1 &&
        controlled.comparison.flow_version_id === seededFlow.version.id &&
        new Set(controlled.comparison.siblings.map((sibling) => sibling.input_fingerprint)).size === 1 &&
        siblingRuns.length === 4 && siblingRuns.every((run) => run.relation_kind === "comparison" && run.model_override) &&
        siblingRuns.every((run) => verifyChain(run)) &&
        // Proof of control is rendered, both columns of it, with every
        // uncontrolled variable carrying its stated reason.
        controlledPanel.text.includes(seededFlow.version.id) &&
        controlledPanel.text.includes(controlled.comparison.input_fingerprint) &&
        controlledPanel.enforced === controlled.comparison.control.enforced_and_verified.length &&
        controlledPanel.uncontrolled === controlled.comparison.control.not_controllable_here.length &&
        controlled.comparison.control.not_controllable_here.every((entry) =>
          controlledPanel.text.includes(entry.variable) && controlledPanel.text.includes(entry.reason)
        ) &&
        // A sweep is never "the score", and the surface says so structurally.
        controlledPanel.text.includes("cross_model_sweep") &&
        controlledPanel.text.includes("usable_as_baseline · false") &&
        controlledPanel.text.includes("not a ranking") &&
        controlledPanel.verdicts === 1 && controlledPanel.alerts === 0 &&
        controlledPanel.rows === 2 && controlledPanel.compromisedRows === 0 &&
        // The forecast is stated before the credit is spent.
        controlled.forecast.includes("2 models × 1 repetition = 2 sibling Runs") &&
        // The aliased sweep: refused as a result, and impossible to read as one.
        aliased.comparison.usable === false &&
        Boolean(aliasProblem) && aliasProblem.requested !== aliasProblem.answered &&
        aliasedPanel.alerts === 1 && aliasedPanel.usableVerdicts === 0 &&
        aliasedPanel.problems === aliased.comparison.integrity_problems.length &&
        aliasedPanel.compromisedRows === 1 &&
        aliasedPanel.listBadge === "Unusable" &&
        aliasedPanel.text.includes("not a result and must not be presented as one") &&
        aliasedPanel.text.includes("response_model_mismatch") &&
        aliasedPanel.text.includes(aliasProblem.answered),
      {
        controlled: {
          id: controlled.comparison.id,
          usable: controlled.comparison.usable,
          pinned_versions: [...new Set(controlled.comparison.siblings.map((sibling) => sibling.flow_version_id))],
          enforced: controlledPanel.enforced,
          uncontrolled: controlledPanel.uncontrolled,
          rows: controlledPanel.rows
        },
        aliased: {
          id: aliased.comparison.id,
          usable: aliased.comparison.usable,
          problems: aliased.comparison.integrity_problems.map((problem) => problem.code),
          alerts: aliasedPanel.alerts,
          compromised_rows: aliasedPanel.compromisedRows
        },
        sibling_runs: siblingRuns.length
      }
    );

    progress("creating a controlled failure and proving forward-only maintenance");
    await navigate(page, "Actions");
    await page.getByRole("button", { name: "New Action" }).click();
    await fieldControl(page.locator(".registry-editor"), "Name", "input").fill("Recovery evidence store");
    await page.locator(".registry-editor").getByLabel("Executor kind").selectOption("data_store");
    await page.locator(".registry-editor").getByRole("tab", { name: "Execution" }).click();
    await page.locator(".registry-editor").getByLabel("Executor config").fill(JSON.stringify({ operation: "append_record", collection: "recovery-evidence", write_enabled: false }, null, 2));
    await publishResource(page, "Publish v1");
    snapshot = await waitForSnapshot(page, (value) => value.studio.actions.some((item) => item.slug === "recovery-evidence-store"), "blocked Action creation");
    const recoveryAction = snapshot.studio.actions.find((item) => item.slug === "recovery-evidence-store");

    await navigate(page, "Flow Studio");
    await page.getByRole("button", { name: "New Flow" }).click();
    await page.locator(".empty-canvas").waitFor({ state: "visible" });
    await page.getByLabel("Search node library").fill("Recovery evidence store");
    await page.locator(".palette-card").filter({ hasText: "Recovery evidence store" }).click();
    await clickCanvasPane(page);
    await fieldControl(page.locator(".node-inspector"), "Name", "input").fill("Browser recovery Flow");
    await fieldControl(page.locator(".node-inspector"), "Purpose", "textarea").fill("Prove that an authority denial becomes a bounded successor and linked proof Run.");
    await clickAndWait(page, page.getByRole("button", { name: "Publish Flow" }));
    snapshot = await waitForSnapshot(page, (value) => value.studio.flows.some((item) => item.slug === "browser-recovery-flow"), "recovery Flow");
    const recoveryFlow = snapshot.studio.flows.find((item) => item.slug === "browser-recovery-flow");
    await page.getByRole("button", { name: "Run", exact: true }).click();
    await page.getByRole("button", { name: "Pin and start Run" }).click();
    await awaitSurface(page, ".runs-page", "starting the Run");
    snapshot = await waitForSnapshot(page, (value) => value.studio.runs.some((run) => run.flow_id === recoveryFlow.id && run.status === "blocked"), "blocked recovery Run");
    const recoveryRoot = snapshot.studio.runs.find((run) => run.flow_id === recoveryFlow.id && !run.parent_run_id);

    progress("ratifying a dead end over repeated independent Runs and proving the brake refuses the next one");
    const startRecoveryRun = async () => {
      await navigate(page, "Flow Studio");
      await page.locator("#flow-select").selectOption(recoveryFlow.id);
      await page.getByRole("button", { name: "Run", exact: true }).click();
      await clickAndWait(page, page.getByRole("button", { name: "Pin and start Run" }));
    };
    const blockedRoots = (value) => value.studio.runs.filter((run) => run.flow_id === recoveryFlow.id && !run.parent_run_id && run.status === "blocked");
    for (const attempt of [2, 3]) {
      await startRecoveryRun();
      await awaitSurface(page, ".runs-page", "starting the Run");
      snapshot = await waitForSnapshot(page, (value) => blockedRoots(value).length >= attempt, `blocked recovery Run ${attempt}`);
    }
    const ratified = snapshot.studio.runs.find((run) => run.flow_id === recoveryFlow.id && !run.parent_run_id).dead_ends[0];
    await page.locator(".dead-end-callout").waitFor({ state: "visible" });
    const deadEndPanel = {
      state: (await page.locator(".dead-end-list > li .badge").first().innerText()).trim(),
      count: (await page.locator(".dead-end-list > li .dead-end-count").first().innerText()).trim(),
      citations: await page.locator(".dead-end-list > li .dead-end-citations button").count()
    };
    record(
      "Run detail surfaces the derived dead end as canonical with its distinct-Run count and citing Run links",
      ratified.ratification_state === "canonical" && ratified.distinct_runs === 3 && ratified.citing_run_ids.length === 3 &&
        deadEndPanel.state.toLowerCase() === "canonical" && deadEndPanel.count === "3 distinct Runs" && deadEndPanel.citations === 3,
      { ...deadEndPanel, derived_state: ratified.ratification_state, derived_distinct_runs: ratified.distinct_runs, node: ratified.node_id }
    );
    if (options.artifacts) await capture(page, resolve(ROOT, options.artifacts, "09-dead-end-panel.png"));

    await startRecoveryRun();
    await page.locator(".brake-refusal").waitFor({ state: "visible" });
    const refusal = {
      text: await page.locator(".brake-refusal").innerText(),
      modals: await page.locator(".modal").count(),
      root_runs: blockedRoots(await workspaceSnapshot(page)).length
    };
    record(
      "the next Run on the canonical path is refused before creation and the refusal cites the three prior Runs",
      refusal.root_runs === 3 && refusal.modals === 0 &&
        refusal.text.includes("refused before it was created") &&
        refusal.text.includes("No Run, no Step, no effect was created") &&
        ratified.citing_run_ids.every((id) => refusal.text.includes(shortId(id, 14))),
      { root_runs: refusal.root_runs, modals: refusal.modals, cited_runs: ratified.citing_run_ids.length, characters: refusal.text.length }
    );
    if (options.artifacts) await capture(page, resolve(ROOT, options.artifacts, "10-brake-refusal.png"));
    await clickAndWait(page, page.getByRole("button", { name: "Dismiss the brake refusal" }));

    progress("distilling a principle across independent Flows and proving that publishing a matching Flow is advised, never refused");
    // A dead end needs one Flow repeating itself; a principle needs three
    // *different* Flows sharing a structure. The recovery Flow above supplies
    // the first, so two more independent Flows reach quorum.
    const publishDeniedFlow = async (name, purpose) => {
      await navigate(page, "Flow Studio");
      await page.getByRole("button", { name: "New Flow" }).click();
      await page.locator(".empty-canvas").waitFor({ state: "visible" });
      await page.getByLabel("Search node library").fill("Recovery evidence store");
      await page.locator(".palette-card").filter({ hasText: "Recovery evidence store" }).click();
      await clickCanvasPane(page);
      await fieldControl(page.locator(".node-inspector"), "Name", "input").fill(name);
      await fieldControl(page.locator(".node-inspector"), "Purpose", "textarea").fill(purpose);
      await clickAndWait(page, page.getByRole("button", { name: "Publish Flow" }));
      return waitForSnapshot(page, (value) => value.studio.flows.some((item) => item.name === name), `published Flow ${name}`);
    };
    const runSelectedFlow = async (flowId) => {
      await page.getByRole("button", { name: "Run", exact: true }).click();
      await clickAndWait(page, page.getByRole("button", { name: "Pin and start Run" }));
      await awaitSurface(page, ".runs-page", "starting the Run");
      return waitForSnapshot(
        page,
        (value) => value.studio.runs.some((run) => run.flow_id === flowId && run.status === "blocked"),
        `blocked Run on Flow ${flowId}`
      );
    };
    for (const [name, purpose] of [
      ["Independent intake ledger", "An unrelated Flow that happens to carry the same disabled declared write."],
      ["Independent audit ledger", "A third independent Flow sharing only the structure, not the definition."]
    ]) {
      snapshot = await publishDeniedFlow(name, purpose);
      snapshot = await runSelectedFlow(snapshot.studio.flows.find((item) => item.name === name).id);
    }
    snapshot = await waitForSnapshot(page, (value) => value.studio.principles.length === 1, "distilled principle");
    const principle = snapshot.studio.principles[0];

    const advisedName = "Advised delivery Flow";
    snapshot = await publishDeniedFlow(advisedName, "Published while a matching principle already exists, to prove publishing is never gated.");
    const advisedFlow = snapshot.studio.flows.find((item) => item.name === advisedName);
    await page.locator(".publish-advisory").waitFor({ state: "visible" });
    const advisoryPanel = {
      text: await page.locator(".publish-advisory").innerText(),
      alerts: await page.locator(".publish-advisory[role='alert']").count(),
      refusals: await page.locator(".brake-refusal, .error-banner").count(),
      modals: await page.locator(".modal").count(),
      run_enabled: await page.getByRole("button", { name: "Run", exact: true }).isEnabled(),
      citations: await page.locator(".publish-advisory .dead-end-citations button").count()
    };
    if (options.artifacts) await capture(page, resolve(ROOT, options.artifacts, "11-publish-advisory.png"));

    // A citation is only evidence if it opens. Click the first one and confirm
    // the Runs console lands on that exact Run.
    await clickAndWait(page, page.locator(".publish-advisory .dead-end-citations button").first());
    const citationJump = {
      runs_view: await page.locator(".runs-page").count(),
      selected: (await page.locator(".run-detail-header h2").innerText()).trim(),
      expected: shortId(principle.citing_run_ids[0], 13)
    };

    await navigate(page, "Flow Studio");
    await page.locator("#flow-select").selectOption(advisedFlow.id);
    const advisedRun = (await runSelectedFlow(advisedFlow.id)).studio.runs.find((run) => run.flow_id === advisedFlow.id);
    await navigate(page, "Overview");
    await page.locator(".principles-section").waitFor({ state: "visible" });
    // `visible` only means present and unhidden. Bring it into the viewport so
    // the captured artifact actually shows the panel it is named after.
    await page.locator(".principles-section").scrollIntoViewIfNeeded();
    await waitIdle(page);
    const principlesPanel = {
      text: await page.locator(".principles-section").innerText(),
      entries: await page.locator(".principle-list > li").count(),
      markers: await page.locator(".principle-ceiling code").count()
    };
    if (options.artifacts) await capture(page, resolve(ROOT, options.artifacts, "12-workspace-principles.png"));
    record(
      "a Flow matching a distilled principle publishes successfully and is advised rather than refused, and the workspace surface states the rule and derives its vocabulary ceiling from the shipped predicate table",
      advisedFlow.current_version === 1 &&
        advisoryPanel.refusals === 0 && advisoryPanel.modals === 0 && advisoryPanel.alerts === 0 &&
        advisoryPanel.run_enabled === true &&
        advisoryPanel.text.includes("This Flow will run") &&
        advisoryPanel.text.includes(principle.statement) &&
        advisoryPanel.citations === principle.citing_run_ids.length &&
        principle.citing_run_ids.every((id) => advisoryPanel.text.includes(shortId(id, 14))) &&
        citationJump.runs_view === 1 && citationJump.selected === citationJump.expected &&
        advisedRun.status === "blocked" && advisedRun.steps.length >= 1 &&
        principlesPanel.entries === 1 &&
        principlesPanel.text.includes(principle.statement) &&
        // The ceiling is derived from the vocabulary the server ships, so assert
        // against that number rather than a phrase that breaks when it grows.
        principlesPanel.markers === snapshot.studio.policy_markers.length &&
        snapshot.studio.policy_markers.every((marker) =>
          principlesPanel.text.includes(`${marker.executor_kind}.${marker.config_key}`)
        ),
      {
        published_version: advisedFlow.current_version,
        advised_run: advisedRun.status,
        advised_run_steps: advisedRun.steps.length,
        distinct_flows: principle.distinct_flows,
        citations: advisoryPanel.citations,
        citation_opened: citationJump.selected,
        run_still_offered: advisoryPanel.run_enabled,
        refusal_surfaces: advisoryPanel.refusals,
        principle_entries: principlesPanel.entries
      }
    );

    await navigate(page, "Runs");
    await page.locator(".run-list-item").filter({ hasText: shortId(recoveryRoot.id) }).first().click();
    await page.getByRole("tab", { name: /^Maintenance/ }).click();
    await clickAndWait(page, page.getByRole("button", { name: "Diagnose Run" }));
    snapshot = await waitForSnapshot(page, (value) => value.studio.runs.some((run) => run.id === recoveryRoot.id && run.diagnosis), "evidence diagnosis");
    await clickAndWait(page, page.getByRole("button", { name: "Generate proposal" }));
    snapshot = await waitForSnapshot(page, (value) => value.studio.runs.some((run) => run.id === recoveryRoot.id && run.repair), "repair proposal");
    await page.getByRole("button", { name: "Review and apply" }).click();
    await page.getByText("I approve this exact successor patch", { exact: true }).click();
    await clickAndWait(page, page.getByRole("button", { name: "Publish successors" }));
    snapshot = await waitForSnapshot(page, (value) => value.studio.runs.some((run) => run.id === recoveryRoot.id && run.repair?.status === "applied"), "successor application");
    await clickAndWait(page, page.getByRole("button", { name: "Run proof" }));
    snapshot = await waitForSnapshot(page, (value) => {
      return value.studio.runs.some((run) => run.parent_run_id === recoveryRoot.id && run.relation_kind === "proof" && run.status === "completed");
    }, "linked proof Run");
    const maintainedRoot = snapshot.studio.runs.find((run) => run.id === recoveryRoot.id);
    const proofRun = snapshot.studio.runs.find((run) => run.parent_run_id === recoveryRoot.id && run.relation_kind === "proof");
    const ownedEventIds = new Set(maintainedRoot.events.map((event) => event.id));
    const repairedAction = snapshot.studio.actions.find((item) => item.id === recoveryAction.id);
    record(
      "maintenance cites owned evidence, appends bounded successors, preserves failure, and proves changed behavior",
      maintainedRoot.status === "blocked" &&
        maintainedRoot.diagnosis.evidence_event_ids.every((id) => ownedEventIds.has(id)) &&
        maintainedRoot.repair.status === "applied" &&
        repairedAction.current_version === 2 &&
        repairedAction.version.config.write_enabled === true &&
        proofRun.relation_kind === "proof" && proofRun.status === "completed" &&
        maintainedRoot.effects.length === 0 && proofRun.effects.length === 1 &&
        verifyChain(maintainedRoot) && verifyChain(proofRun),
      {
        root: maintainedRoot.status,
        action_versions: repairedAction.versions.map((item) => item.version),
        proof: proofRun.status,
        root_effects: maintainedRoot.effects.length,
        proof_effects: proofRun.effects.length
      }
    );
    if (options.artifacts) {
      await page.locator(".run-list-item").filter({ hasText: shortId(recoveryRoot.id) }).first().click();
      await page.getByRole("tab", { name: /^Maintenance/ }).click();
      await capture(page, resolve(ROOT, options.artifacts, "07-maintenance-proof.png"));
    }

    progress("refusing a completion the evidence does not support, then admitting the same pinned version when it does");
    // The stop seam, in the two directions that make it a claim rather than a
    // slogan. One seeded Flow version declares that finishing means the record
    // reached the evidence ledger; a readiness below the gate routes away from
    // that node, so the declared evidence is never minted and the Run is refused.
    // The identical pinned version, given input that reaches the node, completes.
    // Nothing about the contract or the judge changes between the two — only the
    // data — which is the whole reason `completion_unevidenced` is a property of
    // a Run and not of a definition.
    //
    // Exactly one model call per Run, both times: the Flow pins no model-backed
    // node, so the adjudication is the entire spend and this beat costs the
    // journey two calls.
    snapshot = await workspaceSnapshot(page);
    let contractedFlow = snapshot.studio.flows.find((item) => item.slug === "contracted-evidence-publication");
    await navigate(page, "Flow Studio");
    await page.locator("#flow-select").selectOption(contractedFlow.id);
    await ensureInspector(page);
    await clickCanvasPane(page);
    const completionEditor = page.locator(".completion-contract-editor");
    const existingCriteria = await completionEditor.locator(".criterion-editor").count();
    const pinnedJudge = await completionEditor.getByLabel("Independent Goal-Judge").inputValue();
    await completionEditor.getByRole("button", { name: "Add completion criterion" }).click();
    const authoredCriterion = completionEditor.locator(".criterion-editor").last();
    const criterionIdInput = fieldControl(authoredCriterion, "Criterion ID", "input");
    await criterionIdInput.fill("");
    await criterionIdInput.focus();
    await page.keyboard.type("readiness-evaluated");
    await fieldControl(authoredCriterion, "Promise", "textarea").fill("The deterministic readiness gate completed and recorded its routing decision.");
    if (options.artifacts) {
      await completionEditor.scrollIntoViewIfNeeded();
      await capture(page, resolve(ROOT, options.artifacts, "14-completion-contract-authoring.png"));
    }
    await clickAndWait(page, page.getByRole("button", { name: "Publish successor" }));
    snapshot = await waitForSnapshot(page, (value) => value.studio.flows.some((item) => item.id === contractedFlow.id && item.current_version === 2), "authored completion contract successor");
    contractedFlow = snapshot.studio.flows.find((item) => item.id === contractedFlow.id);
    const declaredCriteria = contractedFlow.version.acceptance_criteria;
    record(
      "completion contracts are authorable: a successor pins the Judge and a new promise, evidence kind, and site",
      existingCriteria === 2 &&
        pinnedJudge === contractedFlow.version.judge_agent_version_id &&
        declaredCriteria.length === 3 &&
        declaredCriteria.some((criterion) => criterion.id === "readiness-evaluated" && criterion.evidence_kind === "step" && criterion.node_ids.includes("readiness-gate")),
      { judge: contractedFlow.version.judge_agent_version_id, criteria: declaredCriteria }
    );
    const contractedRecord = "Build Week launch note submitted for the public evidence ledger.";
    const seenContractedRuns = new Set();
    const statusHistory = (run) => run.events.filter((event) => event.type === "run.status_changed").map((event) => event.payload.to);
    const completionEvents = (run) => run.events.filter((event) => event.type.startsWith("completion."));
    const isTerminal = (run) => ["completed", "failed", "blocked", "cancelled"].includes(run.status);
    const contractedRun = async (readiness, label) => {
      await navigate(page, "Flow Studio");
      await page.locator("#flow-select").selectOption(contractedFlow.id);
      await page.getByRole("button", { name: "Run", exact: true }).click();
      await fieldControl(page.locator(".modal"), "Run input", "textarea").fill(JSON.stringify({ record: contractedRecord, readiness }, null, 2));
      await page.getByRole("button", { name: "Pin and start Run" }).click();
      // A refused mutation would otherwise expire as a bare locator timeout on
      // the Runs view instead of reporting the server's own sentence.
      await awaitSurface(page, ".runs-page", `starting the ${label} contracted Run`);
      const fresh = (value) => value.studio.runs.find((run) => run.flow_id === contractedFlow.id && !seenContractedRuns.has(run.id) && isTerminal(run));
      const latest = await waitForSnapshot(page, (value) => Boolean(fresh(value)), `terminal ${label} contracted Run`);
      const run = fresh(latest);
      seenContractedRuns.add(run.id);
      // Select this Run explicitly rather than trusting the console's default.
      // A citation earlier in the journey asked the console to focus one exact
      // Run, and that request is honoured again on every remount — so arriving
      // here from the Flow Studio would otherwise land on the cited Run and the
      // adjudication asserted below would be read off the wrong evidence.
      await page.locator(".run-list-item").filter({ hasText: shortId(run.id) }).first().click();
      await waitIdle(page);
      return run;
    };

    const refusedRun = await contractedRun(0.31, "refused");
    const refusedEvents = completionEvents(refusedRun);
    record(
      "a seeded Flow declares what finishing means, and a Run whose input never reached the declared work is refused instead of reported finished",
      declaredCriteria.length === 3 &&
        contractedFlow.version.judge_agent_version_id !== null &&
        refusedRun.status === "failed" &&
        refusedRun.error_code === "completion_unevidenced" &&
        // Never completed, not merely not completed now: a post-hoc annotation
        // on an already-finished Run would satisfy the final row and nothing a
        // user cares about.
        !statusHistory(refusedRun).includes("completed") &&
        refusedRun.output === null &&
        refusedEvents.length === 1 && refusedEvents[0].type === "completion.refused" &&
        refusedEvents[0].payload.admitted === false &&
        declaredCriteria.filter((criterion) => criterion.id !== "readiness-evaluated").every((criterion) => refusedEvents[0].payload.unevidenced.includes(criterion.id)) &&
        !refusedEvents[0].payload.unevidenced.includes("readiness-evaluated") &&
        refusedEvents[0].payload.judge_claim?.assessment &&
        // The work it did do is untouched, and the work it never did is absent.
        refusedRun.effects.length === 0 &&
        refusedRun.steps.map((step) => step.node_id).join(",") === "readiness-gate,hold-for-revision" &&
        // One adjudication, and the ledger still verifies across it.
        refusedRun.model_calls.length === 1 &&
        verifyChain(refusedRun),
      {
        run: refusedRun.id,
        status: refusedRun.status,
        error_code: refusedRun.error_code,
        status_history: statusHistory(refusedRun),
        unevidenced: refusedEvents[0]?.payload?.unevidenced,
        effects: refusedRun.effects.length,
        model_calls: refusedRun.model_calls.length
      }
    );

    await awaitSurface(page, ".completion-callout", "the refused adjudication");
    await page.locator(".completion-callout").scrollIntoViewIfNeeded();
    // These two frames are submission assets, so let the transient success
    // toast clear rather than capturing it parked over the verdict.
    await page.locator(".toast").waitFor({ state: "hidden" });
    await waitIdle(page);
    const refusalPanel = {
      text: await page.locator(".completion-callout").innerText(),
      tone: await page.locator(".completion-callout.tone-danger").count(),
      criteria: await page.locator(".completion-callout .criterion-list > li").count(),
      unevidenced: await page.locator(".completion-callout .criterion-list > li.is-unevidenced").count()
    };
    if (options.artifacts) await capture(page, resolve(ROOT, options.artifacts, "15-completion-refused.png"));
    record(
      "the Runs console renders the stop-seam refusal and names every declared promise that carried no accepted evidence",
      refusalPanel.tone === 1 &&
        refusalPanel.criteria === declaredCriteria.length &&
        refusalPanel.unevidenced === 2 &&
        refusalPanel.text.includes("Completion refused") &&
        refusalPanel.text.includes("Model claim · non-authoritative") &&
        refusalPanel.text.includes("completion_unevidenced") &&
        declaredCriteria.every((criterion) => refusalPanel.text.includes(criterion.id) && refusalPanel.text.includes(criterion.statement)) &&
        refusalPanel.text.includes("publish-to-ledger"),
      { ...refusalPanel, characters: refusalPanel.text.length }
    );

    const admittedRun = await contractedRun(0.92, "admitted");
    const admittedEvents = completionEvents(admittedRun);
    await awaitSurface(page, ".completion-callout.tone-success", "the admitted adjudication");
    await page.locator(".completion-callout").scrollIntoViewIfNeeded();
    // These two frames are submission assets, so let the transient success
    // toast clear rather than capturing it parked over the verdict.
    await page.locator(".toast").waitFor({ state: "hidden" });
    await waitIdle(page);
    const admissionPanelText = await page.locator(".completion-callout").innerText();
    if (options.artifacts) await capture(page, resolve(ROOT, options.artifacts, "16-completion-admitted.png"));
    record(
      "the identical pinned Flow version admits when the Run actually reached the declared work, so the refusal was about this Run's data and not the definition",
      admittedRun.flow_version_id === refusedRun.flow_version_id &&
        admittedRun.flow_fingerprint === refusedRun.flow_fingerprint &&
        admittedRun.status === "completed" &&
        admittedRun.error_code === null &&
        statusHistory(admittedRun).join(",") === "running,completed" &&
        admittedEvents.length === 1 && admittedEvents[0].type === "completion.admitted" &&
        admittedEvents[0].payload.admitted === true &&
        admittedEvents[0].payload.unevidenced.length === 0 &&
        admittedEvents[0].payload.criteria.every((criterion) => criterion.holds && criterion.surviving.length >= 1) &&
        admittedRun.effects.length === 1 &&
        admittedRun.model_calls.length === 1 &&
        admissionPanelText.includes("Completion admitted") &&
        verifyChain(admittedRun),
      {
        refused: refusedRun.id,
        admitted: admittedRun.id,
        shared_flow_version: admittedRun.flow_version_id,
        effects: admittedRun.effects.length,
        model_calls: admittedRun.model_calls.length,
        adjudications_added_by_this_beat: refusedRun.model_calls.length + admittedRun.model_calls.length
      }
    );

    progress("checking documentation, accessibility, motion, and responsive layout");
    await navigate(page, "Documentation");
    const documentationText = await page.locator(".docs-page").innerText();
    const requiredDocumentation = [
      "twelve outputs", "ai is visible", "first-class node",
      "observe and control work as runs", "evidence decides whether it becomes true",
      "non-authoritative", "canonical", "before provider i/o", "forward recovery",
      "capability forge", "provenance is not performance", "browser tab", "public boundary",
      "smartread", "parallel boardrooms", "governed memory"
    ];
    const normalizedDocumentation = documentationText.toLowerCase();
    const missingDocumentation = requiredDocumentation.filter((phrase) => !normalizedDocumentation.includes(phrase));
    record(
      "live documentation explains authoring, authority, stop truth, ratification, comparison controls, maintenance, BYOK, and public limits",
      missingDocumentation.length === 0,
      { characters: documentationText.length, missing: missingDocumentation }
    );

    const buttonNames = await page.locator("button").evaluateAll((items) => ({
      total: items.length,
      unnamed: items.filter((button) => !(button.textContent?.trim() || button.getAttribute("aria-label") || button.getAttribute("title"))).length
    }));
    record("every rendered button has an accessible name", buttonNames.unnamed === 0, buttonNames);

    const contrastResults = [];
    for (const theme of ["light", "dark"]) {
      await page.evaluate((value) => { document.documentElement.dataset.theme = value; }, theme);
      for (const view of ["Overview", "Flow Studio", "BoardRooms", "Context & Memory", "Actions", "Agents", "Prompts", "Skills", "Runs", "Capability Forge", "Comparisons", "Documentation", "Settings"]) {
        await navigate(page, view);
        contrastResults.push({ theme, view, ...await auditTextContrast(page) });
      }
    }
    const contrastFailures = contrastResults.filter((result) => result.failureCount);
    record(
      "visible text clears WCAG AA contrast across every workbench in both themes",
      contrastFailures.length === 0,
      {
        samples: contrastResults.reduce((total, result) => total + result.tested, 0),
        minimum: Math.min(...contrastResults.map((result) => result.minimum)),
        failures: contrastFailures
      }
    );
    await page.evaluate(() => { document.documentElement.dataset.theme = "light"; });

    await navigate(page, "Flow Studio");
    await page.emulateMedia({ reducedMotion: "reduce" });
    const reducedDuration = await page.locator(".kyn-node").first().evaluate((element) => getComputedStyle(element).transitionDuration);
    record("reduced-motion preference collapses interaction transitions", reducedDuration.split(",").every((duration) => parseFloat(duration) <= 0.001), reducedDuration);

    await page.setViewportSize({ width: 390, height: 844 });
    await page.reload({ waitUntil: "networkidle" });
    await page.locator(".app-shell").waitFor({ state: "visible" });
    const mobile = await page.evaluate(() => {
      const node = document.querySelector(".react-flow__node")?.getBoundingClientRect();
      const minimap = document.querySelector(".react-flow__minimap")?.getBoundingClientRect();
      return {
      clientWidth: document.documentElement.clientWidth,
      scrollWidth: document.documentElement.scrollWidth,
      nodes: document.querySelectorAll(".react-flow__node").length,
      mainTop: Math.round(document.querySelector("#main-content").getBoundingClientRect().top),
      renderedNodeWidth: node ? Math.round(node.width) : 0,
      minimapWidth: minimap ? Math.round(minimap.width) : 0
      };
    });
    record("390px reload preserves a legible pannable graph without page overflow", mobile.clientWidth === 390 && mobile.scrollWidth === 390 && mobile.nodes >= 1 && mobile.renderedNodeWidth >= 180 && mobile.minimapWidth <= 120, mobile);
    if (options.artifacts) await capture(page, resolve(ROOT, options.artifacts, "08-mobile-workbench.png"));

    const securityResponse = await fetch(`${baseUrl}/app/`);
    record(
      "server sends restrictive security and no-store headers",
      securityResponse.headers.get("content-security-policy")?.includes("object-src 'none'") && securityResponse.headers.get("cache-control") === "no-store"
    );
    const nonLocalRequests = requestedUrls.filter((url) => {
      try {
        const parsed = new URL(url);
        return ["http:", "https:"].includes(parsed.protocol) && parsed.origin !== baseUrl;
      } catch {
        return false;
      }
    });
    record("browser makes no cross-origin runtime request", nonLocalRequests.length === 0, nonLocalRequests);
    record("browser journey has no failed request", failedRequests.length === 0, failedRequests);
    record("browser journey has no console or page error", pageErrors.length === 0, pageErrors);
    record("the only refused HTTP response in the journey is the one asserted brake 409", brakeRefusals.length === 1, brakeRefusals);
  } catch (error) {
    fatalError = error;
    if (!checks.some((check) => check.status === "fail")) checks.push({ name: "Playwright verification completed", status: "fail", detail: error.message });
  } finally {
    await browser?.close();
    server?.kill("SIGTERM");
    await delay(160);
    rmSync(runtimeTemp, { recursive: true, force: true });
  }

  const failed = checks.filter((check) => check.status === "fail");
  const report = {
    generated_at: new Date().toISOString(),
    surface: "Kyn.ist Agent Studio full Playwright product journey",
    runtime: {
      chromium: findChromium(),
      provider: localTarget ? "deterministic provider-shaped seam" : "deployed OpenAI runtime",
      base_url: baseUrl,
      viewport_matrix: ["1440x1000", "390x844"]
    },
    summary: { checks: checks.length, passed: checks.length - failed.length, failed: failed.length },
    checks,
    diagnostics: {
      server_output: serverOutput.join("").trim().split("\n").slice(-20),
      page_errors: pageErrors,
      failed_requests: failedRequests,
      brake_refusals: brakeRefusals
    }
  };
  if (options.report) {
    const reportPath = resolve(ROOT, options.report);
    mkdirSync(dirname(reportPath), { recursive: true });
    writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`);
  }
  console.log(JSON.stringify(report, null, 2));
  return fatalError || failed.length ? 1 : 0;
}

process.exitCode = await main();
