#!/usr/bin/env node

import { spawn } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import net from "node:net";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const TIMEOUT_MS = 180_000;
const checks = [];

function delay(milliseconds) {
  return new Promise((resolveDelay) => setTimeout(resolveDelay, milliseconds));
}

function record(name, condition, detail = null) {
  checks.push({ name, status: condition ? "pass" : "fail", detail });
  if (!condition) {
    throw new Error(`check failed: ${name}${detail ? ` (${JSON.stringify(detail)})` : ""}`);
  }
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
    await delay(75);
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

class CdpClient {
  constructor(webSocketUrl) {
    this.socket = new WebSocket(webSocketUrl);
    this.nextId = 1;
    this.pending = new Map();
    this.listeners = new Map();
  }

  async connect() {
    await new Promise((resolveOpen, reject) => {
      const timeout = setTimeout(() => reject(new Error("CDP WebSocket open timeout")), TIMEOUT_MS);
      this.socket.addEventListener("open", () => {
        clearTimeout(timeout);
        resolveOpen();
      }, { once: true });
      this.socket.addEventListener("error", () => {
        clearTimeout(timeout);
        reject(new Error("CDP WebSocket failed to open"));
      }, { once: true });
    });
    this.socket.addEventListener("message", (event) => this.handleMessage(event));
  }

  handleMessage(event) {
    const message = JSON.parse(String(event.data));
    if (message.id) {
      const pending = this.pending.get(message.id);
      if (!pending) return;
      this.pending.delete(message.id);
      if (message.error) pending.reject(new Error(`${pending.method}: ${message.error.message}`));
      else pending.resolve(message.result ?? {});
      return;
    }
    for (const listener of this.listeners.get(message.method) ?? []) listener(message.params ?? {});
  }

  on(method, listener) {
    const listeners = this.listeners.get(method) ?? [];
    listeners.push(listener);
    this.listeners.set(method, listeners);
  }

  send(method, params = {}) {
    const id = this.nextId++;
    return new Promise((resolveResult, reject) => {
      const timeout = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`${method}: response timeout`));
      }, TIMEOUT_MS);
      this.pending.set(id, {
        method,
        resolve: (value) => {
          clearTimeout(timeout);
          resolveResult(value);
        },
        reject: (error) => {
          clearTimeout(timeout);
          reject(error);
        }
      });
      this.socket.send(JSON.stringify({ id, method, params }));
    });
  }

  waitFor(method, predicate = () => true) {
    return new Promise((resolveEvent, reject) => {
      const timer = setTimeout(() => reject(new Error(`${method}: event timeout`)), TIMEOUT_MS);
      const listener = (params) => {
        if (!predicate(params)) return;
        clearTimeout(timer);
        const listeners = this.listeners.get(method) ?? [];
        this.listeners.set(method, listeners.filter((candidate) => candidate !== listener));
        resolveEvent(params);
      };
      this.on(method, listener);
    });
  }

  close() {
    this.socket.close();
  }
}

async function evaluate(client, expression) {
  const result = await client.send("Runtime.evaluate", {
    expression,
    awaitPromise: true,
    returnByValue: true,
    userGesture: true
  });
  if (result.exceptionDetails) {
    throw new Error(result.exceptionDetails.exception?.description ?? "browser evaluation failed");
  }
  return result.result?.value;
}

async function waitForExpression(client, expression, timeout = TIMEOUT_MS) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await evaluate(client, expression)) return;
    await delay(75);
  }
  throw new Error(`browser condition timed out: ${expression}`);
}

async function navigate(client, url) {
  const loaded = client.waitFor("Page.loadEventFired");
  const result = await client.send("Page.navigate", { url });
  if (result.errorText) throw new Error(`navigation failed: ${result.errorText}`);
  await loaded;
}

async function click(client, selector) {
  await evaluate(client, `document.querySelector(${JSON.stringify(selector)})?.click()`);
}

async function capture(client, path) {
  const result = await client.send("Page.captureScreenshot", {
    format: "png",
    fromSurface: true,
    captureBeyondViewport: false
  });
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, Buffer.from(result.data, "base64"));
}

function parseArgs() {
  const args = process.argv.slice(2);
  const options = { report: null, artifacts: null, baseUrl: null };
  for (let index = 0; index < args.length; index += 1) {
    if (args[index] === "--report") options.report = args[++index];
    else if (args[index] === "--artifacts") options.artifacts = args[++index];
    else if (args[index] === "--base-url") {
      const parsed = new URL(args[++index]);
      if (!["http:", "https:"].includes(parsed.protocol)) throw new Error("base URL must use HTTP(S)");
      if (parsed.username || parsed.password || parsed.search || parsed.hash || parsed.pathname !== "/") {
        throw new Error("base URL must be an origin without credentials, path, query, or fragment");
      }
      options.baseUrl = parsed.origin;
    } else throw new Error(`unknown argument: ${args[index]}`);
  }
  return options;
}

async function main() {
  const options = parseArgs();
  const localTarget = options.baseUrl === null;
  const appPort = localTarget ? await freePort() : null;
  const debugPort = await freePort();
  const baseUrl = options.baseUrl ?? `http://127.0.0.1:${appPort}`;
  const profile = mkdtempSync(resolve(tmpdir(), "kyn-flight-recorder-chromium-"));
  const runtimeTemp = mkdtempSync(resolve(tmpdir(), "kyn-flight-recorder-runtime-"));
  const serverOutput = [];
  const browserOutput = [];
  let server = null;
  let browser = null;
  let client = null;
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

    const health = await (await waitForHttp(`${baseUrl}/healthz`)).json();
    record("runtime health exposes SQLite and configured model transport", health.sqlite === "ready" && health.openai_configured === true, health);

    browser = spawn(findChromium(), [
      "--headless=new",
      "--no-sandbox",
      "--disable-gpu",
      "--disable-extensions",
      "--disable-background-networking",
      "--disable-component-update",
      "--disable-default-apps",
      "--disable-sync",
      "--metrics-recording-only",
      "--no-first-run",
      "--no-default-browser-check",
      `--remote-debugging-port=${debugPort}`,
      `--user-data-dir=${profile}`,
      "about:blank"
    ], { stdio: ["ignore", "ignore", "pipe"] });
    browser.stderr.on("data", (chunk) => browserOutput.push(String(chunk)));

    await waitForHttp(`http://127.0.0.1:${debugPort}/json/version`);
    const targets = await (await waitForHttp(`http://127.0.0.1:${debugPort}/json/list`)).json();
    const pageTarget = targets.find((target) => target.type === "page");
    if (!pageTarget?.webSocketDebuggerUrl) throw new Error("Chromium page target not found");

    client = new CdpClient(pageTarget.webSocketDebuggerUrl);
    await client.connect();
    const pageErrors = [];
    const failedRequests = [];
    const requestedUrls = [];
    client.on("Runtime.exceptionThrown", ({ exceptionDetails }) => {
      pageErrors.push(exceptionDetails?.exception?.description ?? exceptionDetails?.text ?? "page exception");
    });
    client.on("Runtime.consoleAPICalled", ({ type, args }) => {
      if (type === "error") pageErrors.push(args.map((arg) => arg.value ?? arg.description).join(" "));
    });
    client.on("Network.loadingFailed", ({ errorText, canceled }) => {
      if (!canceled) failedRequests.push(errorText);
    });
    client.on("Network.requestWillBeSent", ({ request }) => requestedUrls.push(request.url));

    await Promise.all([
      client.send("Page.enable"),
      client.send("Runtime.enable"),
      client.send("Network.enable"),
      client.send("Accessibility.enable")
    ]);
    await client.send("Emulation.setDeviceMetricsOverride", {
      width: 1440,
      height: 1000,
      deviceScaleFactor: 1,
      mobile: false
    });

    await navigate(client, `${baseUrl}/app/`);
    await waitForExpression(client, `document.querySelector("#onboarding")?.hidden === false`);
    const onboarding = await evaluate(client, `({
      heading: document.querySelector(".hero-copy h1")?.textContent.replace(/\\s+/g, " ").trim(),
      steps: document.querySelectorAll(".hero-proof li").length,
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      unnamedButtons: [...document.querySelectorAll("button")].filter((button) => !(button.getAttribute("aria-label") || button.textContent.trim())).length
    })`);
    record("onboarding explains the complete causal loop", onboarding.steps === 7 && onboarding.heading.includes("proven repair"), onboarding);
    record("desktop onboarding has no horizontal overflow", onboarding.overflow === 0, onboarding.overflow);
    record("every browser control has an accessible name", onboarding.unnamedButtons === 0, onboarding.unnamedButtons);
    if (options.artifacts) await capture(client, resolve(ROOT, options.artifacts, "01-compose.png"));

    await click(client, "#create-lab");
    await waitForExpression(client, `document.querySelector("#runtime")?.hidden === false && document.querySelector("#primary-action")?.textContent.includes("Run real agent flow")`);
    const composed = await evaluate(client, `({
      prompts: document.querySelectorAll("#prompt-list .resource-card").length,
      skills: document.querySelectorAll("#skill-list .resource-card").length,
      agents: document.querySelectorAll("#agent-list .resource-card").length,
      model: document.querySelector("#flow-model")?.textContent,
      policy: document.querySelector("#allowed-environments")?.textContent,
      requested: document.querySelector("#requested-environment")?.textContent
    })`);
    record("workspace renders versioned agents prompts and skills", composed.prompts === 3 && composed.skills === 3 && composed.agents === 3, composed);
    record("composed v1 manifest makes the intended mismatch visible", composed.policy.includes("staging") && composed.requested.includes("Production"), composed);

    await click(client, "#primary-action");
    await waitForExpression(client, `document.querySelector("#primary-action")?.textContent.includes("Diagnose from evidence")`);
    record("real executor flow reaches an authoritative blocked state", await evaluate(client, `document.querySelector("#run-status")?.textContent.trim() === "Blocked"`));
    if (options.artifacts) await capture(client, resolve(ROOT, options.artifacts, "02-blocked.png"));

    await click(client, "#primary-action");
    await waitForExpression(client, `document.querySelector("#primary-action")?.textContent.includes("Propose bounded repair")`);
    const diagnosis = await evaluate(client, `({
      visible: document.querySelector("#diagnosis-panel")?.hidden === false,
      fault: document.querySelector("#diagnosis-class")?.textContent,
      confidence: document.querySelector("#diagnosis-confidence")?.textContent,
      citations: document.querySelector("#diagnosis-citations")?.textContent
    })`);
    record("forensic agent diagnosis is evidence-grounded", diagnosis.visible && diagnosis.fault === "Policy Mismatch" && diagnosis.confidence === "High" && diagnosis.citations.startsWith("2"), diagnosis);

    await click(client, "#primary-action");
    await waitForExpression(client, `document.querySelector("#primary-action")?.textContent.includes("Review human approval fence")`);
    const repair = await evaluate(client, `({
      visible: document.querySelector("#repair-panel")?.hidden === false,
      path: document.querySelector("#repair-path")?.textContent,
      revision: document.querySelector("#repair-revision")?.textContent,
      patch: document.querySelector("#patch-add")?.textContent
    })`);
    record("repair agent is limited to one manifest path and revision", repair.visible && repair.path === "/policy/allowed_environments" && repair.revision === "1" && repair.patch.includes("production"), repair);

    await click(client, "#primary-action");
    await waitForExpression(client, `document.querySelector("#approval-dialog")?.open === true`);
    const dialog = await evaluate(client, `({
      focused: document.activeElement?.id,
      revision: document.querySelector("#dialog-revision")?.textContent,
      proposalHash: document.querySelector("#dialog-proposal-hash")?.textContent,
      acknowledged: document.querySelector("#approval-acknowledged")?.checked
    })`);
    record("human fence opens focused and pins hash plus revision", dialog.focused === "approval-actor" && dialog.revision === "1 → 2" && dialog.proposalHash.length >= 32 && dialog.acknowledged === false, dialog);
    await evaluate(client, `(() => {
      const checkbox = document.querySelector("#approval-acknowledged");
      checkbox.checked = true;
      checkbox.dispatchEvent(new Event("change", { bubbles: true }));
      document.querySelector("#submit-approval").click();
    })()`);
    await waitForExpression(client, `document.querySelector("#primary-action")?.textContent.includes("Rerun against flow v2")`);
    record("human command creates immutable flow v2", await evaluate(client, `document.querySelector("#flow-version")?.textContent.includes("v2")`));

    await click(client, "#primary-action");
    await waitForExpression(client, `document.querySelector("#primary-action")?.textContent.includes("Closed loop proven")`);
    const proof = await evaluate(client, `(async () => {
      const snapshot = (await fetch("/api/v1/workspace", { credentials: "same-origin" }).then((response) => response.json())).data;
      const root = snapshot.runs.find((run) => !run.parent_run_id);
      const child = snapshot.runs.find((run) => run.parent_run_id === root.id);
      const owned = new Set(root.events.map((event) => event.id));
      const chains = [root, child].every((run) => run.events.every((event, index) =>
        event.sequence === index + 1 && (index === 0 || event.prev_hash === run.events[index - 1].event_hash)
      ));
      return {
        rootStatus: root.status,
        childStatus: child.status,
        rootVersion: root.flow_version,
        childVersion: child.flow_version,
        linked: child.parent_run_id === root.id,
        rootEffects: root.sandbox_effects.length,
        childEffects: child.sandbox_effects.length,
        diagnosisOwned: root.diagnosis.evidence_event_ids.every((id) => owned.has(id)),
        approved: root.repair.approval.acknowledged,
        approvalActor: root.repair.approval.actor,
        chains,
        runCards: document.querySelectorAll(".run-card .run-card-header").length,
        ledgerSwitches: document.querySelectorAll("#run-switcher button").length,
        selectedLedger: document.querySelector("#run-switcher .is-active")?.textContent,
        phase: document.querySelector("#phase-caption")?.textContent
      };
    })()`);
    record("child rerun proves a changed effect without rewriting v1", proof.rootStatus === "blocked" && proof.childStatus === "completed" && proof.rootVersion === 1 && proof.childVersion === 2 && proof.linked && proof.rootEffects === 0 && proof.childEffects === 1, proof);
    record("diagnosis citations approval and both hash chains remain authoritative", proof.diagnosisOwned && proof.approved && proof.approvalActor === "build-week-judge" && proof.chains, proof);
    record("UI exposes before after proof and both ledgers", proof.runCards === 2 && proof.ledgerSwitches === 2 && proof.selectedLedger === "Child rerun" && proof.phase.includes("proof complete"), proof);
    await evaluate(client, `document.querySelector("#runs-section")?.scrollIntoView()`);
    await delay(120);
    if (options.artifacts) await capture(client, resolve(ROOT, options.artifacts, "03-proven-repair.png"));

    const axTree = await client.send("Accessibility.getFullAXTree");
    const namedButtons = axTree.nodes.filter((node) => node.role?.value === "button");
    record("accessibility tree exposes named buttons", namedButtons.length >= 8 && namedButtons.every((node) => String(node.name?.value ?? "").trim() !== ""), { buttons: namedButtons.length });

    await client.send("Emulation.setEmulatedMedia", {
      features: [{ name: "prefers-reduced-motion", value: "reduce" }]
    });
    const reduced = await evaluate(client, `getComputedStyle(document.querySelector("#primary-action")).transitionDuration`);
    record("reduced motion collapses interaction transitions", reduced.split(",").every((duration) => parseFloat(duration) <= 0.001), reduced);

    await client.send("Emulation.setDeviceMetricsOverride", {
      width: 390,
      height: 844,
      deviceScaleFactor: 1,
      mobile: true
    });
    const reloaded = client.waitFor("Page.loadEventFired");
    await client.send("Page.reload", { ignoreCache: true });
    await reloaded;
    await waitForExpression(client, `document.querySelector("#primary-action")?.textContent.includes("Closed loop proven")`);
    const mobile = await evaluate(client, `({
      viewport: document.documentElement.clientWidth,
      scrollWidth: document.documentElement.scrollWidth,
      railHidden: getComputedStyle(document.querySelector(".rail")).display === "none",
      actionWidth: Math.round(document.querySelector("#primary-action").getBoundingClientRect().width),
      errorHidden: document.querySelector("#error-panel").hidden
    })`);
    record("390px reload preserves proof without document overflow", mobile.viewport === 390 && mobile.scrollWidth === 390 && mobile.railHidden && mobile.actionWidth <= 356 && mobile.errorHidden, mobile);
    if (options.artifacts) await capture(client, resolve(ROOT, options.artifacts, "04-mobile-proof.png"));

    const response = await fetch(`${baseUrl}/app/`);
    record("server sends restrictive security and no-store headers", response.headers.get("content-security-policy")?.includes("object-src 'none'") && response.headers.get("cache-control") === "no-store");

    const nonLocalRequests = requestedUrls.filter((url) => {
      try {
        const parsed = new URL(url);
        return ["http:", "https:"].includes(parsed.protocol) && parsed.origin !== baseUrl;
      } catch {
        return false;
      }
    });
    record("browser makes no cross-origin runtime request", nonLocalRequests.length === 0, nonLocalRequests);
    record("browser journey has no failed requests", failedRequests.length === 0, failedRequests);
    record("browser journey has no console or page errors", pageErrors.length === 0, pageErrors);
  } catch (error) {
    fatalError = error;
    if (!checks.some((check) => check.status === "fail")) {
      checks.push({ name: "browser verification completed", status: "fail", detail: error.message });
    }
  } finally {
    client?.close();
    browser?.kill("SIGTERM");
    server?.kill("SIGTERM");
    await delay(150);
    rmSync(profile, { recursive: true, force: true });
    rmSync(runtimeTemp, { recursive: true, force: true });
  }

  const failed = checks.filter((check) => check.status === "fail");
  const report = {
    generated_at: new Date().toISOString(),
    surface: "Kyn.ist Flight Recorder closed-loop browser journey",
    runtime: {
      chromium: findChromium(),
      provider: localTarget ? "deterministic provider-shaped seam" : "deployed OpenAI runtime",
      base_url: baseUrl,
      viewport_matrix: ["1440x1000", "390x844"]
    },
    summary: { checks: checks.length, passed: checks.length - failed.length, failed: failed.length },
    checks,
    diagnostics: {
      server_output: serverOutput.join("").trim().split("\n").slice(-12),
      browser_output: browserOutput.join("").trim().split("\n").filter(Boolean).slice(-8)
    }
  };

  if (options.report) {
    const reportPath = resolve(ROOT, options.report);
    mkdirSync(dirname(reportPath), { recursive: true });
    writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`);
  }
  console.log(JSON.stringify(report, null, 2));
  if (fatalError || failed.length > 0) return 1;
  return 0;
}

process.exitCode = await main();
