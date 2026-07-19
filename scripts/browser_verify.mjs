#!/usr/bin/env node

import { spawn } from "node:child_process";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  rmSync,
  writeFileSync
} from "node:fs";
import net from "node:net";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const TIMEOUT_MS = 60_000;
const checks = [];
const APPROVAL_DEMO_BRIEF =
  "Launch a Build Week preview for judges. A 20–4000 character brief enters a " +
  "pinned GPT-5.6 Agent, which must return summary:string, score:number from 0 to 1, " +
  "and risks:string[]. A deterministic gate requires score >= 0.75. Passing work " +
  "must pause for an attributable human decision; approval may append exactly one " +
  "idempotent row only to this workspace's SQLite approved_launches sandbox. Success " +
  "means the model call, typed Steps, Action receipts, decision, hash-linked events, " +
  "and effect are inspectable, with zero effects before approval.";

function record(name, condition, detail = null) {
  checks.push({ name, status: condition ? "pass" : "fail", detail });
  if (!condition) {
    throw new Error(`check failed: ${name}${detail ? ` (${JSON.stringify(detail)})` : ""}`);
  }
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
      server.close(() => {
        if (port === null) reject(new Error("failed to allocate a loopback port"));
        else resolvePort(port);
      });
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
    await delay(80);
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
    } else {
      throw new Error(`unknown argument: ${argument}`);
    }
  }
  return options;
}

async function workspaceSnapshot(page) {
  return page.evaluate(async () => {
    const response = await fetch("/api/v1/workspace", {
      credentials: "same-origin",
      headers: { Accept: "application/json" }
    });
    return (await response.json()).data;
  });
}

function verifyChain(run) {
  return run.events.every(
    (event, index) =>
      event.sequence === index + 1 &&
      (index === 0 || event.prev_hash === run.events[index - 1].event_hash)
  );
}

async function capture(page, path) {
  mkdirSync(dirname(path), { recursive: true });
  await page.screenshot({ path, fullPage: false });
}

async function waitUntilReady(page) {
  await page.waitForFunction(() => document.body.dataset.busy === "false");
}

async function clickAndWait(page, selector) {
  await page.locator(selector).click();
  await waitUntilReady(page);
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
        [
          "-m",
          "scripts.browser_test_server",
          "--port",
          String(appPort),
          "--database",
          resolve(runtimeTemp, "browser.sqlite3")
        ],
        { cwd: ROOT, stdio: ["ignore", "pipe", "pipe"] }
      );
      server.stdout.on("data", (chunk) => serverOutput.push(String(chunk)));
      server.stderr.on("data", (chunk) => serverOutput.push(String(chunk)));
    }

    const healthResponse = await waitForHttp(`${baseUrl}/healthz`);
    const health = await healthResponse.json();
    record(
      "runtime exposes SQLite plus browser-session BYOK and official SDK transport",
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
      const expectedBootstrapMiss =
        text.includes("Failed to load resource") && text.includes("401");
      if (message.type() === "error" && !expectedBootstrapMiss) pageErrors.push(text);
    });
    page.on("request", (request) => requestedUrls.push(request.url()));
    page.on("requestfailed", (request) => {
      failedRequests.push(`${request.method()} ${request.url()} ${request.failure()?.errorText ?? "failed"}`);
    });

    await page.goto(`${baseUrl}/app/`, { waitUntil: "networkidle" });
    await page.locator("#onboarding").waitFor({ state: "visible" });
    const onboarding = {
      title: await page.locator(".hero-copy h1").innerText(),
      previewNodes: await page.locator(".preview-flow article").count(),
      overflow: await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    };
    record(
      "onboarding presents a configurable agent builder rather than a scripted demo",
      onboarding.title.includes("Build agent systems") && onboarding.previewNodes === 4,
      onboarding
    );
    record("desktop onboarding has no document overflow", onboarding.overflow === 0, onboarding.overflow);
    if (options.artifacts) await capture(page, resolve(ROOT, options.artifacts, "01-agent-studio.png"));

    await clickAndWait(page, "#create-workspace");
    await page.locator("#overview-view").waitFor({ state: "visible" });
    const initial = await workspaceSnapshot(page);
    record(
      "workspace seeds the complete bounded Action palette and one editable Flow",
      initial.studio.actions.length >= 8 &&
        new Set(initial.studio.actions.map((action) => action.version.kind)).size >= 8 &&
        initial.studio.flows.length >= 1,
      {
        actions: initial.studio.actions.length,
        flows: initial.studio.flows.length,
        kinds: initial.studio.actions.map((action) => action.version.kind)
      }
    );

    await page.locator('[data-view="actions"]').click();
    await page.locator("#create-action").click();
    await page.locator("#action-dialog").waitFor({ state: "visible" });
    await page.locator("#action-name").fill("Browser greeting");
    await page.locator("#action-slug").fill("browser-greeting");
    if (options.artifacts) await capture(page, resolve(ROOT, options.artifacts, "02-action-contract.png"));
    await page.locator("#action-form button[type='submit']").click();
    await waitUntilReady(page);
    await page.waitForFunction(() => document.querySelectorAll("#action-list .definition-card").length >= 6);
    let snapshot = await workspaceSnapshot(page);
    const browserAction = snapshot.studio.actions.find((action) => action.slug === "browser-greeting");
    record(
      "browser can define a typed immutable Action",
      browserAction?.version.kind === "template" && browserAction.version.input_schema.required[0] === "name",
      browserAction ? { id: browserAction.id, version: browserAction.version.id } : null
    );

    await page.locator('[data-view="flows"]').click();
    await page.locator("#create-flow").click();
    await page.locator(".visual-builder.is-editing").waitFor({ state: "visible" });
    await page.locator("[data-flow-settings]").click();
    await page.locator('[data-flow-setting="name"]').fill("Browser greeting Flow");
    await page.locator('[data-flow-setting="slug"]').fill("browser-greeting-flow");
    if (options.artifacts) await capture(page, resolve(ROOT, options.artifacts, "02-visual-flow-builder.png"));
    await page.locator("[data-publish-flow-draft]").click();
    await waitUntilReady(page);
    await page.waitForFunction(() => document.querySelectorAll("#flow-list .selection-button").length >= 3);
    snapshot = await workspaceSnapshot(page);
    const deterministicFlow = snapshot.studio.flows.find((flow) => flow.slug === "browser-greeting-flow");
    record(
      "browser composes a user-defined version-pinned Flow",
      deterministicFlow?.version.nodes.length === 1 &&
        deterministicFlow.version.nodes[0].version_id === browserAction.version.id &&
        deterministicFlow.version.requires_model === false,
      deterministicFlow ? { id: deterministicFlow.id, nodes: deterministicFlow.version.nodes.length } : null
    );

    await page.locator("#flow-inspector [data-run-flow]").click();
    await page.locator("#run-dialog").waitFor({ state: "visible" });
    await page.locator("#run-form button[type='submit']").click();
    await waitUntilReady(page);
    await page.waitForFunction(() => document.querySelector("#run-inspector .status-completed"));
    snapshot = await workspaceSnapshot(page);
    const deterministicRun = snapshot.studio.runs.find(
      (run) => run.flow_id === deterministicFlow.id && run.input.name === "Ada"
    );
    record(
      "deterministic Flow executes without a credential and emits authoritative evidence",
      deterministicRun?.status === "completed" &&
        deterministicRun.output?.text === "Hello Ada" &&
        deterministicRun.model_calls.length === 0 &&
        deterministicRun.action_receipts.length === 1 &&
        verifyChain(deterministicRun),
      deterministicRun
        ? {
            id: deterministicRun.id,
            status: deterministicRun.status,
            model_calls: deterministicRun.model_calls.length,
            receipts: deterministicRun.action_receipts.length
          }
        : null
    );

    await page.locator('[data-view="flows"]').click();
    await page.locator("#flow-list .selection-button", { hasText: "Browser greeting Flow" }).click();
    await page.locator("[data-add-trigger]").click();
    await page.locator("#trigger-form button[type='submit']").click();
    await waitUntilReady(page);
    const hookPath = await page.locator(".webhook-reveal code").innerText();
    const webhookResult = await page.evaluate(async (url) => {
      const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: "Webhook" })
      });
      return (await response.json()).data;
    }, hookPath);
    record(
      "a public webhook trigger executes its pinned deterministic Flow version",
      webhookResult?.run?.status === "completed" &&
        webhookResult.run.flow_version === 1 &&
        webhookResult.run.output?.text === "Hello Webhook",
      webhookResult?.run
        ? { status: webhookResult.run.status, flow_version: webhookResult.run.flow_version }
        : null
    );
    await page.locator(`[data-toggle-trigger="${webhookResult.trigger_id}"]`).click();
    await waitUntilReady(page);
    snapshot = await workspaceSnapshot(page);
    const disabledTrigger = snapshot.studio.triggers.find(
      (trigger) => trigger.id === webhookResult.trigger_id
    );
    record(
      "trigger operations can disable a binding through an optimistic revision fence",
      disabledTrigger?.enabled === false && disabledTrigger.revision === 2,
      disabledTrigger
        ? { enabled: disabledTrigger.enabled, revision: disabledTrigger.revision }
        : null
    );

    await page.locator("[data-edit-flow]").click();
    await page.locator(".retry-editor input[type='number']").first().fill("2");
    await page.locator(".retry-editor input[type='number']").first().blur();
    await page.locator("[data-publish-flow-draft]").click();
    await waitUntilReady(page);
    snapshot = await workspaceSnapshot(page);
    const revisedDeterministicFlow = snapshot.studio.flows.find(
      (flow) => flow.id === deterministicFlow.id
    );
    const unchangedDeterministicRun = snapshot.studio.runs.find(
      (run) => run.id === deterministicRun.id
    );
    record(
      "canvas editing publishes a successor while prior Runs keep their exact graph pin",
      revisedDeterministicFlow?.version.version === 2 &&
        revisedDeterministicFlow.version.nodes[0].settings.max_attempts === 2 &&
        unchangedDeterministicRun?.flow_version === 1 &&
        unchangedDeterministicRun.flow_graph.nodes[0].settings.max_attempts === 1,
      {
        current_version: revisedDeterministicFlow?.version.version,
        prior_run_version: unchangedDeterministicRun?.flow_version,
        prior_run_attempt_policy: unchangedDeterministicRun?.flow_graph.nodes[0].settings.max_attempts
      }
    );

    await page.locator("#open-config").click();
    const browserKey = localTarget
      ? "test-browser-owned-openai-key-for-playwright"
      : process.env.OPENAI_API_KEY;
    if (!browserKey) throw new Error("OPENAI_API_KEY is required for deployed browser verification");
    await page.locator("#openai-api-key").fill(browserKey);
    await page.locator("#save-api-key").click();
    record(
      "configuration keeps the key in this tab and exposes no key text in the page",
      (await page.evaluate(() => sessionStorage.getItem("kyn.openai.api-key.v1")?.length ?? 0)) >= 20 &&
        !(await page.locator("body").innerText()).includes(browserKey),
      { configured: true }
    );

    await page.locator('[data-view="flows"]').click();
    const seededFlowButton = page.locator("#flow-list .selection-button", { hasText: "Agent-reviewed launch" });
    await seededFlowButton.click();
    await page.locator("#flow-inspector [data-run-flow]").click();
    await page.locator("#run-input").fill(JSON.stringify({ brief: APPROVAL_DEMO_BRIEF }, null, 2));
    await page.locator("#run-form button[type='submit']").click();
    await waitUntilReady(page);
    await page.waitForFunction(() => document.querySelector("#run-inspector .status-waiting_approval"));
    snapshot = await workspaceSnapshot(page);
    const seededFlow = snapshot.studio.flows.find((flow) => flow.slug === "agent-reviewed-launch");
    let aiRun = snapshot.studio.runs.find(
      (run) => run.flow_id === seededFlow?.id && !run.parent_run_id
    );
    record(
      "AI Flow uses pinned Agent stack and pauses at a real Human approval",
      aiRun?.steps.length === 3 &&
        aiRun.model_calls.length >= 1 &&
        aiRun.pending_approval !== null &&
        aiRun.effects.length === 0 &&
        verifyChain(aiRun),
      aiRun
        ? {
            id: aiRun.id,
            status: aiRun.status,
            steps: aiRun.steps.map((step) => step.status),
            model_calls: aiRun.model_calls.length,
            effects: aiRun.effects.length
          }
        : null
    );
    if (options.artifacts) await capture(page, resolve(ROOT, options.artifacts, "02-waiting-approval.png"));

    await page.locator("[data-approved='true']").click();
    await page.locator("#approval-dialog").waitFor({ state: "visible" });
    await page.locator("#submit-approval").click();
    await waitUntilReady(page);
    snapshot = await workspaceSnapshot(page);
    aiRun = snapshot.studio.runs.find((run) => run.id === aiRun.id);
    record(
      "approval resumes the pinned graph into exactly one bounded sandbox effect",
      aiRun.status === "completed" &&
        aiRun.pending_approval === null &&
        aiRun.effects.length === 1 &&
        aiRun.approvals[0].decision?.approved === true &&
        verifyChain(aiRun),
      {
        status: aiRun.status,
        effects: aiRun.effects.length,
        decision: aiRun.approvals[0].decision?.approved
      }
    );

    await page.locator(`[data-rerun='${aiRun.id}']`).click();
    await waitUntilReady(page);
    snapshot = await workspaceSnapshot(page);
    let child = snapshot.studio.runs.find((run) => run.parent_run_id === aiRun.id);
    record(
      "rerun creates a linked child against the same immutable Flow version",
      child?.status === "waiting_approval" &&
        child.flow_version_id === aiRun.flow_version_id &&
        child.correlation_id === aiRun.correlation_id,
      child
        ? {
            id: child.id,
            parent: child.parent_run_id,
            flow_version_id: child.flow_version_id
          }
        : null
    );
    await page.locator("[data-approved='true']").click();
    await page.locator("#submit-approval").click();
    await waitUntilReady(page);
    snapshot = await workspaceSnapshot(page);
    child = snapshot.studio.runs.find((run) => run.id === child.id);
    record(
      "linked child completes independently with its own valid event chain",
      child.status === "completed" && child.effects.length === 1 && verifyChain(child),
      { status: child.status, effects: child.effects.length, events: child.events.length }
    );
    if (options.artifacts) await capture(page, resolve(ROOT, options.artifacts, "03-run-evidence.png"));

    await page.locator('[data-view="resources"]').click();
    progress("creating first-class Prompt, Skill, and Agent resources");
    await page.locator('[data-resource-tab="prompts"]').click();
    await page.locator("#create-resource").click();
    await page.locator("#prompt-name").fill("Browser Prompt");
    await page.locator("#prompt-form button[type='submit']").click();
    await waitUntilReady(page);
    await page.locator('[data-resource-tab="skills"]').click();
    await page.locator("#create-resource").click();
    await page.locator("#skill-name").fill("Browser Skill");
    await page.locator("#skill-form button[type='submit']").click();
    await waitUntilReady(page);
    await page.locator('[data-resource-tab="agents"]').click();
    await page.locator("#create-resource").click();
    await page.locator("#agent-name").fill("Browser Agent");
    await page.locator("#agent-form button[type='submit']").click();
    await waitUntilReady(page);
    snapshot = await workspaceSnapshot(page);
    record(
      "Prompt Skill and Agent creation are first-class browser workflows",
      snapshot.prompts.some((item) => item.name === "Browser Prompt") &&
        snapshot.skills.some((item) => item.name === "Browser Skill") &&
        snapshot.agents.some((item) => item.name === "Browser Agent"),
      {
        prompts: snapshot.prompts.length,
        skills: snapshot.skills.length,
        agents: snapshot.agents.length
      }
    );

    progress("building a blocked visual Flow for integrated maintenance");
    await page.locator('[data-view="actions"]').click();
    await page.locator("#create-action").click();
    await page.locator("#action-kind").selectOption("data_store");
    await page.locator("#action-name").fill("Recovery evidence store");
    await page.locator("#action-slug").fill("recovery-evidence-store");
    const deniedConfig = JSON.parse(await page.locator("#action-config").inputValue());
    deniedConfig.write_enabled = false;
    await page.locator("#action-config").fill(JSON.stringify(deniedConfig, null, 2));
    await page.locator("#action-form button[type='submit']").click();
    await waitUntilReady(page);

    await page.locator('[data-view="flows"]').click();
    await page.locator("#create-flow").click();
    await page.locator("[data-flow-settings]").click();
    await page.locator('[data-flow-setting="name"]').fill("Browser recovery Flow");
    await page.locator('[data-flow-setting="slug"]').fill("browser-recovery-flow");
    await page.locator(".palette-item", { hasText: "Recovery evidence store" }).click();
    await page.locator("[data-publish-flow-draft]").click();
    await waitUntilReady(page);
    await page.locator("#flow-inspector [data-run-flow]").click();
    await page.locator("#run-form button[type='submit']").click();
    await waitUntilReady(page);
    await page.waitForFunction(() => document.querySelector("#run-inspector .status-blocked"));

    progress("diagnosing, proposing, and approving the bounded repair");
    await clickAndWait(page, "[data-diagnose-studio-run]");
    await clickAndWait(page, "[data-propose-studio-repair]");
    await page.locator("[data-approve-studio-repair]").click();
    await page.locator("#studio-repair-ack").check();
    await page.locator("#studio-repair-form button[type='submit']").click();
    await waitUntilReady(page);
    if (options.artifacts) await capture(page, resolve(ROOT, options.artifacts, "04-repair-approved.png"));
    progress("executing linked proof Run");
    await clickAndWait(page, "[data-prove-studio-repair]");
    snapshot = await workspaceSnapshot(page);
    const recoveryFlow = snapshot.studio.flows.find((flow) => flow.slug === "browser-recovery-flow");
    const repairRoot = snapshot.studio.runs.find(
      (run) => run.flow_id === recoveryFlow.id && !run.parent_run_id
    );
    const repairChild = snapshot.studio.runs.find((run) => run.parent_run_id === repairRoot.id);
    const ownedIds = new Set(repairRoot.events.map((event) => event.id));
    record(
      "integrated Run maintenance proves evidence-owned diagnosis bounded repair and linked outcome",
      repairRoot.status === "blocked" &&
        repairRoot.diagnosis.evidence_event_ids.every((id) => ownedIds.has(id)) &&
        repairRoot.repair.status === "applied" &&
        repairRoot.repair.applied_flow_version === 2 &&
        repairChild.status === "completed" &&
        repairRoot.effects.length === 0 &&
        repairChild.effects.length === 1 &&
        verifyChain(repairRoot) &&
        verifyChain(repairChild),
      {
        parent_status: repairRoot.status,
        child_status: repairChild.status,
        citations: repairRoot.diagnosis.evidence_event_ids.length,
        parent_effects: repairRoot.effects.length,
        child_effects: repairChild.effects.length
      }
    );
    if (options.artifacts) {
      await page.locator(`#run-list [data-select-run="${repairRoot.id}"]`).click();
      await capture(page, resolve(ROOT, options.artifacts, "05-repair-proven.png"));
    }

    const buttons = await page.locator("button").evaluateAll((items) =>
      items.map((button) => ({
        text: button.textContent.trim(),
        label: button.getAttribute("aria-label") ?? ""
      }))
    );
    record(
      "every browser button has an accessible name",
      buttons.every((button) => button.text || button.label),
      { buttons: buttons.length }
    );

    await page.emulateMedia({ reducedMotion: "reduce" });
    const reducedDuration = await page.locator(".graph-node").first().evaluate(
      (element) => getComputedStyle(element).transitionDuration
    );
    record(
      "reduced motion collapses interaction transitions",
      reducedDuration.split(",").every((duration) => parseFloat(duration) <= 0.001),
      reducedDuration
    );

    await page.setViewportSize({ width: 390, height: 844 });
    await page.reload({ waitUntil: "networkidle" });
    await page.locator("#workspace-surface").waitFor({ state: "visible" });
    const mobile = await page.evaluate(() => ({
      viewport: document.documentElement.clientWidth,
      scrollWidth: document.documentElement.scrollWidth,
      sidebarHeight: Math.round(document.querySelector("#sidebar").getBoundingClientRect().height),
      errorHidden: document.querySelector("#error-panel").hidden
    }));
    record(
      "390px reload preserves the workspace without document overflow",
      mobile.viewport === 390 &&
        mobile.scrollWidth === 390 &&
        mobile.sidebarHeight < 90 &&
        mobile.errorHidden,
      mobile
    );
    if (options.artifacts) await capture(page, resolve(ROOT, options.artifacts, "06-mobile-studio.png"));

    const securityResponse = await fetch(`${baseUrl}/app/`);
    record(
      "server sends restrictive security and no-store headers",
      securityResponse.headers.get("content-security-policy")?.includes("object-src 'none'") &&
        securityResponse.headers.get("cache-control") === "no-store"
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
    if (!checks.some((check) => check.status === "fail")) {
      checks.push({ name: "Playwright verification completed", status: "fail", detail: error.message });
    }
  } finally {
    await browser?.close();
    server?.kill("SIGTERM");
    await delay(150);
    rmSync(runtimeTemp, { recursive: true, force: true });
  }

  const failed = checks.filter((check) => check.status === "fail");
  const report = {
    generated_at: new Date().toISOString(),
    surface: "Kyn.ist Agent Studio full Playwright journey",
    runtime: {
      chromium: findChromium(),
      provider: localTarget ? "deterministic provider-shaped seam" : "deployed OpenAI runtime",
      base_url: baseUrl,
      viewport_matrix: ["1440x1000", "390x844"]
    },
    summary: {
      checks: checks.length,
      passed: checks.length - failed.length,
      failed: failed.length
    },
    checks,
    diagnostics: {
      server_output: serverOutput.join("").trim().split("\n").slice(-14),
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
  if (fatalError || failed.length) return 1;
  return 0;
}

process.exitCode = await main();
