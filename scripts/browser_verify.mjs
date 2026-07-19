#!/usr/bin/env node

import { spawn } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import net from "node:net";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";
import { APPROVAL_DEMO_BRIEF } from "../src/lib.js";

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
      if (message.type() === "error" && !expectedBootstrapMiss) pageErrors.push(text);
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

    await page.getByRole("button", { name: "Run", exact: true }).click();
    await fieldControl(page.locator(".modal"), "Run input", "textarea").fill(JSON.stringify({ value: "priority" }, null, 2));
    await page.getByRole("button", { name: "Pin and start Run" }).click();
    await page.locator(".runs-page").waitFor({ state: "visible" });
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
    await page.locator(".runs-page").waitFor({ state: "visible" });
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
    await page.locator(".runs-page").waitFor({ state: "visible" });
    snapshot = await waitForSnapshot(page, (value) => value.studio.runs.some((run) => run.flow_id === seededFlow.id && run.status === "waiting_approval"), "AI Run approval pause");
    let aiRun = snapshot.studio.runs.find((run) => run.flow_id === seededFlow.id && run.status === "waiting_approval");
    await page.getByRole("button", { name: "Approve and resume" }).waitFor({ state: "visible" });
    record(
      "AI Flow invokes a pinned Agent stack, records model evidence, and pauses before effects",
      aiRun.model_calls.length >= 1 && aiRun.pending_approval !== null && aiRun.effects.length === 0 && aiRun.steps.some((step) => step.status === "waiting_approval") && verifyChain(aiRun),
      { run: aiRun.id, calls: aiRun.model_calls.length, steps: aiRun.steps.map((step) => step.status), effects: aiRun.effects.length }
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
    await page.locator(".runs-page").waitFor({ state: "visible" });
    snapshot = await waitForSnapshot(page, (value) => value.studio.runs.some((run) => run.flow_id === recoveryFlow.id && run.status === "blocked"), "blocked recovery Run");
    const recoveryRoot = snapshot.studio.runs.find((run) => run.flow_id === recoveryFlow.id && !run.parent_run_id);
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
      await page.locator(".run-list-item").filter({ hasText: "Blocked" }).first().click();
      await page.getByRole("tab", { name: /^Maintenance/ }).click();
      await capture(page, resolve(ROOT, options.artifacts, "07-maintenance-proof.png"));
    }

    progress("checking documentation, accessibility, motion, and responsive layout");
    await navigate(page, "Documentation");
    const documentationText = await page.locator(".docs-page").innerText();
    const requiredDocumentation = ["twelve outputs", "ai is visible", "first-class node", "observe and control work as runs", "forward recovery", "browser tab", "public boundary"];
    const normalizedDocumentation = documentationText.toLowerCase();
    const missingDocumentation = requiredDocumentation.filter((phrase) => !normalizedDocumentation.includes(phrase));
    record(
      "live documentation explains outputs, AI pins, subflows, Run truth, maintenance, BYOK, and public limits",
      missingDocumentation.length === 0,
      { characters: documentationText.length, missing: missingDocumentation }
    );

    const unnamedButtons = await page.locator("button").evaluateAll((items) => items.filter((button) => !(button.textContent?.trim() || button.getAttribute("aria-label") || button.getAttribute("title"))).length);
    record("every rendered button has an accessible name", unnamedButtons === 0, { unnamed: unnamedButtons });

    await navigate(page, "Flow Studio");
    await page.emulateMedia({ reducedMotion: "reduce" });
    const reducedDuration = await page.locator(".kyn-node").first().evaluate((element) => getComputedStyle(element).transitionDuration);
    record("reduced-motion preference collapses interaction transitions", reducedDuration.split(",").every((duration) => parseFloat(duration) <= 0.001), reducedDuration);

    await page.setViewportSize({ width: 390, height: 844 });
    await page.reload({ waitUntil: "networkidle" });
    await page.locator(".app-shell").waitFor({ state: "visible" });
    const mobile = await page.evaluate(() => ({
      clientWidth: document.documentElement.clientWidth,
      scrollWidth: document.documentElement.scrollWidth,
      nodes: document.querySelectorAll(".react-flow__node").length,
      mainTop: Math.round(document.querySelector("#main-content").getBoundingClientRect().top)
    }));
    record("390px reload preserves the workspace and graph without page overflow", mobile.clientWidth === 390 && mobile.scrollWidth === 390 && mobile.nodes >= 1, mobile);
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
      failed_requests: failedRequests
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
