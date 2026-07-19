#!/usr/bin/env node

import { spawn } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import net from "node:net";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const TIMEOUT_MS = 12_000;
const checks = [];
const metrics = {};

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
        if (port === null) reject(new Error("failed to allocate loopback port"));
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
    await delay(60);
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
  if (!executable) {
    throw new Error("Chromium not found; set CHROMIUM_BIN to run browser verification.");
  }
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
    for (const listener of this.listeners.get(message.method) ?? []) {
      listener(message.params ?? {});
    }
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

  waitFor(method, predicate = () => true, timeout = TIMEOUT_MS) {
    return new Promise((resolveEvent, reject) => {
      const timer = setTimeout(() => reject(new Error(`${method}: event timeout`)), timeout);
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
    await delay(50);
  }
  throw new Error(`browser condition timed out: ${expression}`);
}

async function navigate(client, url) {
  const loaded = client.waitFor("Page.loadEventFired");
  const result = await client.send("Page.navigate", { url });
  if (result.errorText) throw new Error(`navigation failed: ${result.errorText}`);
  await loaded;
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

async function pressKey(client, key, code = key) {
  const keyCode = {
    Enter: 13,
    Tab: 9,
    Space: 32,
    ArrowLeft: 37,
    ArrowRight: 39,
    Home: 36,
    End: 35,
    Escape: 27
  }[code] ?? 0;
  const base = {
    key,
    code,
    windowsVirtualKeyCode: keyCode,
    nativeVirtualKeyCode: keyCode
  };
  await client.send("Input.dispatchKeyEvent", { type: "rawKeyDown", ...base });
  if (code === "Enter" || code === "Space") {
    const text = code === "Enter" ? "\r" : " ";
    await client.send("Input.dispatchKeyEvent", {
      type: "char",
      ...base,
      text,
      unmodifiedText: text
    });
  }
  await client.send("Input.dispatchKeyEvent", { type: "keyUp", ...base });
}

function parseArgs() {
  const args = process.argv.slice(2);
  const options = { report: null, artifacts: null, baseUrl: null };
  for (let index = 0; index < args.length; index += 1) {
    if (args[index] === "--report") options.report = args[++index];
    else if (args[index] === "--artifacts") options.artifacts = args[++index];
    else if (args[index] === "--base-url") {
      const rawUrl = args[++index];
      if (!rawUrl) throw new Error("--base-url requires an HTTP(S) origin");
      const parsedUrl = new URL(rawUrl);
      if (!["http:", "https:"].includes(parsedUrl.protocol)) {
        throw new Error("--base-url must use http or https");
      }
      if (parsedUrl.username || parsedUrl.password || parsedUrl.search || parsedUrl.hash) {
        throw new Error("--base-url must not contain credentials, a query, or a fragment");
      }
      if (parsedUrl.pathname !== "/") {
        throw new Error("--base-url must be an origin without a path");
      }
      options.baseUrl = parsedUrl.origin;
    }
    else throw new Error(`unknown argument: ${args[index]}`);
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
  const serverOutput = [];
  const browserOutput = [];
  let server = null;
  let browser = null;
  let client = null;
  let fatalError = null;

  try {
    if (localTarget) {
      server = spawn(process.env.PYTHON_BIN ?? "python3", ["serve.py", "--port", String(appPort)], {
        cwd: ROOT,
        stdio: ["ignore", "pipe", "pipe"]
      });
      server.stdout.on("data", (chunk) => serverOutput.push(String(chunk)));
      server.stderr.on("data", (chunk) => serverOutput.push(String(chunk)));
    }
    await waitForHttp(`${baseUrl}/healthz`);

    const chromium = findChromium();
    browser = spawn(chromium, [
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

    await navigate(client, `${baseUrl}/app/#run`);
    await waitForExpression(client, `document.body.dataset.runStatus === "blocked"`);
    const initial = await evaluate(client, `(() => ({
      status: document.body.dataset.runStatus,
      graphNodes: document.querySelectorAll(".graph-node").length,
      recentEvents: document.querySelectorAll("#recent-events li").length,
      visiblePanel: document.querySelector("[data-view-panel]:not([hidden])")?.dataset.viewPanel,
      rawCredentialInDom: document.documentElement.outerHTML.includes("SYNTHETIC_VALUE") || document.documentElement.outerHTML.includes("REDACTED_BY_FIXTURE"),
      unnamedButtons: [...document.querySelectorAll("button")].filter((button) => !(button.getAttribute("aria-label") || button.textContent.trim())).length,
      horizontalOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      renderMark: performance.getEntriesByName("kyn-render-complete").at(-1)?.startTime ?? null
    }))()`);
    metrics.first_render_ms = initial.renderMark;
    record("initial blocked run renders", initial.status === "blocked" && initial.visiblePanel === "run", initial);
    record("causal graph contains seven evidence nodes", initial.graphNodes === 7, initial.graphNodes);
    record("recent evidence list is populated", initial.recentEvents === 3, initial.recentEvents);
    record("raw fixture credentials never enter the DOM", initial.rawCredentialInDom === false);
    record("all buttons have accessible names", initial.unnamedButtons === 0, initial.unnamedButtons);
    record("desktop document has no horizontal overflow", initial.horizontalOverflow === 0, initial.horizontalOverflow);
    record("first render stays under one second on the tested origin", initial.renderMark !== null && initial.renderMark < 1000, initial.renderMark);

    const axTree = await client.send("Accessibility.getFullAXTree");
    const buttons = axTree.nodes.filter((node) => node.role?.value === "button");
    record(
      "accessibility tree exposes named controls",
      buttons.length >= 15 && buttons.every((node) => String(node.name?.value ?? "").trim() !== ""),
      { buttons: buttons.length }
    );

    await evaluate(client, `document.querySelector('[data-node-id="accepted"]').focus()`);
    await pressKey(client, "ArrowRight", "ArrowRight");
    const graphAfterArrow = await evaluate(client, `({
      focus: document.activeElement?.dataset.nodeId,
      selected: document.querySelector('.graph-node[aria-pressed="true"]')?.dataset.nodeId
    })`);
    await pressKey(client, "End", "End");
    const graphAfterEnd = await evaluate(client, `({
      focus: document.activeElement?.dataset.nodeId,
      selected: document.querySelector('.graph-node[aria-pressed="true"]')?.dataset.nodeId
    })`);
    record(
      "causal graph supports arrow and boundary-key navigation",
      graphAfterArrow.focus === "plan" && graphAfterArrow.selected === "plan" && graphAfterEnd.focus === "queue" && graphAfterEnd.selected === "queue",
      { graphAfterArrow, graphAfterEnd }
    );

    const response = await fetch(`${baseUrl}/app/`);
    record("server sends a restrictive content security policy", response.headers.get("content-security-policy")?.includes("object-src 'none'"));
    record("server disables caching for fixture fidelity", response.headers.get("cache-control") === "no-store");

    if (options.artifacts) await capture(client, resolve(ROOT, options.artifacts, "01-blocked-run.png"));

    await evaluate(client, `document.querySelector("#open-intervention").click()`);
    await waitForExpression(client, `document.querySelector("#intervention-dialog").open === true`);
    const preview = await evaluate(client, `(() => ({
      dialogOpen: document.querySelector("#intervention-dialog").open,
      externalEffect: [...document.querySelectorAll("#dialog-preview dd")].some((node) => node.textContent.includes("None — local simulation")),
      actor: document.querySelector("#dialog-actor").textContent,
      revision: document.querySelector("#dialog-revision").textContent
    }))()`);
    record("command preview is explicit and local-only", preview.dialogOpen && preview.externalEffect, preview);
    record("preview pins actor and revision", preview.actor === "build-week-judge" && preview.revision === "7 → 8", preview);

    await pressKey(client, "Escape", "Escape");
    await waitForExpression(client, `document.querySelector("#intervention-dialog").open === false`);
    await waitForExpression(client, `document.activeElement?.id === "open-intervention"`);
    record(
      "closing the dialog restores its invoking control",
      await evaluate(client, `document.activeElement?.id === "open-intervention"`)
    );
    await pressKey(client, "Enter", "Enter");
    await waitForExpression(client, `document.querySelector("#intervention-dialog").open === true`);
    await waitForExpression(client, `document.activeElement?.id === "intervention-reason"`);
    const instantKeyboardDialog = await evaluate(client, `document.querySelector("#intervention-dialog").dataset.instant`);
    record("keyboard-opened dialog skips motion and focuses the reason", instantKeyboardDialog === "true", instantKeyboardDialog);

    await client.send("Input.insertText", {
      text: "Evidence is verified and the synthetic staging scope is bounded."
    });
    await pressKey(client, "Tab", "Tab");
    const checkboxFocus = await evaluate(client, `document.activeElement?.id`);
    await pressKey(client, " ", "Space");
    const checkboxChecked = await evaluate(client, `document.querySelector("#simulation-acknowledgement").checked`);
    await pressKey(client, "Tab", "Tab");
    const cancelFocus = await evaluate(client, `document.activeElement?.id`);
    await pressKey(client, "Tab", "Tab");
    const applyFocus = await evaluate(client, `document.activeElement?.id`);
    record(
      "keyboard path reaches acknowledgement and both dialog actions",
      checkboxFocus === "simulation-acknowledgement" && checkboxChecked === true && cancelFocus === "cancel-intervention" && applyFocus === "apply-intervention",
      { checkboxFocus, checkboxChecked, cancelFocus, applyFocus }
    );
    await pressKey(client, "Enter", "Enter");
    await waitForExpression(client, `document.body.dataset.runStatus === "completed"`);
    const completed = await evaluate(client, `(() => {
      const receipt = Object.fromEntries(
        [...document.querySelectorAll("#receipt-fields > div")].map((row) => [
          row.querySelector("dt")?.textContent,
          row.querySelector("dd")?.textContent
        ])
      );
      return {
        status: document.body.dataset.runStatus,
        visiblePanel: document.querySelector("[data-view-panel]:not([hidden])")?.dataset.viewPanel,
        ledgerRows: document.querySelectorAll("#audit-table-body tr").length,
        receiptVisible: !document.querySelector("#receipt-content").hidden,
        sessionStored: sessionStorage.getItem("kyn.flight-recorder.session.v1") !== null,
        receipt
      };
    })()`);
    record("authorized intervention completes the run", completed.status === "completed", completed);
    record("intervention appends four events and a receipt", completed.ledgerRows === 9 && completed.receiptVisible, completed);
    record("receipt advances exactly one revision", completed.receipt.Revision === "7 → 8", completed.receipt.Revision);
    record(
      "receipt preserves run and correlation identity",
      completed.receipt.Run === "run_01JY7KYN9X4N" && completed.receipt.Correlation === "corr_01JY7KYN7M2Q",
      { run: completed.receipt.Run, correlation: completed.receipt.Correlation }
    );
    record("session state is locally resumable", completed.sessionStored === true);
    if (options.artifacts) await capture(client, resolve(ROOT, options.artifacts, "02-command-receipt.png"));

    const reloaded = client.waitFor("Page.loadEventFired");
    await client.send("Page.reload", { ignoreCache: true });
    await reloaded;
    await waitForExpression(client, `document.body.dataset.runStatus === "completed"`);
    const persisted = await evaluate(client, `document.querySelectorAll("#audit-table-body tr").length`);
    record("reload returns the idempotent receipt without duplication", persisted === 9, persisted);

    await evaluate(client, `document.querySelector("#reset-demo").click()`);
    await waitForExpression(client, `document.body.dataset.runStatus === "blocked"`);
    const reset = await evaluate(client, `(() => ({
      ledgerRows: document.querySelectorAll("#audit-table-body tr").length,
      session: sessionStorage.getItem("kyn.flight-recorder.session.v1"),
      visiblePanel: document.querySelector("[data-view-panel]:not([hidden])")?.dataset.viewPanel
    }))()`);
    record("reset restores the deterministic initial state", reset.ledgerRows === 5 && reset.session === null && reset.visiblePanel === "run", reset);

    await client.send("Emulation.setEmulatedMedia", {
      features: [{ name: "prefers-reduced-motion", value: "reduce" }]
    });
    const reducedMotion = await evaluate(client, `getComputedStyle(document.querySelector("#open-intervention")).transitionDuration`);
    record("reduced-motion preference collapses interaction duration", reducedMotion.split(",").every((duration) => parseFloat(duration) <= 0.001), reducedMotion);

    await client.send("Emulation.setDeviceMetricsOverride", {
      width: 390,
      height: 844,
      deviceScaleFactor: 1,
      mobile: true
    });
    const mobileReload = client.waitFor("Page.loadEventFired");
    await client.send("Page.reload", { ignoreCache: true });
    await mobileReload;
    await waitForExpression(client, `document.body.dataset.runStatus === "blocked"`);
    const mobile = await evaluate(client, `(() => ({
      viewport: document.documentElement.clientWidth,
      scrollWidth: document.documentElement.scrollWidth,
      navPosition: getComputedStyle(document.querySelector(".sidebar")).position,
      inspectorWidth: Math.round(document.querySelector(".inspector").getBoundingClientRect().width)
    }))()`);
    record("narrow viewport has no document-level overflow", mobile.viewport === 390 && mobile.scrollWidth === 390, mobile);
    record("mobile navigation remains fixed and reachable", mobile.navPosition === "fixed", mobile.navPosition);
    record("mobile inspector stays within viewport", mobile.inspectorWidth <= 364, mobile.inspectorWidth);

    await evaluate(client, `document.querySelector("#open-intervention").click()`);
    await waitForExpression(client, `document.querySelector("#intervention-dialog").open === true`);
    await waitForExpression(client, `document.activeElement?.id === "intervention-reason"`);
    const mobileDialog = await evaluate(client, `(() => {
      const dialog = document.querySelector("#intervention-dialog").getBoundingClientRect();
      const body = document.querySelector(".dialog-body").getBoundingClientRect();
      const footer = document.querySelector(".dialog-footer").getBoundingClientRect();
      const reason = document.querySelector("#intervention-reason").getBoundingClientRect();
      const viewportHeight = window.visualViewport?.height ?? window.innerHeight;
      return {
        width: Math.round(dialog.width),
        dialogBottom: Math.round(dialog.bottom),
        viewportHeight: Math.round(viewportHeight),
        bodyBottom: Math.round(body.bottom),
        footerTop: Math.round(footer.top),
        reasonTop: Math.round(reason.top),
        reasonBottom: Math.round(reason.bottom),
        reasonVisible: reason.top >= body.top - 1 && reason.bottom <= body.bottom + 1,
        focused: document.activeElement?.id
      };
    })()`);
    record("mobile dialog stays within viewport", mobileDialog.width <= 362 && mobileDialog.dialogBottom <= mobileDialog.viewportHeight + 1, mobileDialog);
    record(
      "mobile dialog scroll body does not overlap its actions",
      mobileDialog.bodyBottom <= mobileDialog.footerTop + 1 && mobileDialog.reasonVisible && mobileDialog.focused === "intervention-reason",
      mobileDialog
    );
    await delay(120);
    if (options.artifacts) await capture(client, resolve(ROOT, options.artifacts, "04-mobile-dialog.png"));

    await client.send("Emulation.setDeviceMetricsOverride", {
      width: 390,
      height: 520,
      deviceScaleFactor: 1,
      mobile: true
    });
    await waitForExpression(client, `(window.visualViewport?.height ?? window.innerHeight) <= 520`);
    await pressKey(client, "Tab", "Tab");
    await waitForExpression(client, `(() => {
      const body = document.querySelector(".dialog-body").getBoundingClientRect();
      const row = document.querySelector(".check-row").getBoundingClientRect();
      const control = document.querySelector(".custom-check").getBoundingClientRect();
      return document.activeElement?.id === "simulation-acknowledgement" &&
        row.top >= body.top - 1 && row.bottom <= body.bottom + 1 &&
        control.top >= body.top - 1 && control.bottom <= body.bottom + 1;
    })()`);
    await delay(100);
    await waitForExpression(client, `(() => {
      const body = document.querySelector(".dialog-body").getBoundingClientRect();
      const row = document.querySelector(".check-row").getBoundingClientRect();
      const control = document.querySelector(".custom-check").getBoundingClientRect();
      return document.activeElement?.id === "simulation-acknowledgement" &&
        row.top >= body.top - 1 && row.bottom <= body.bottom + 1 &&
        control.top >= body.top - 1 && control.bottom <= body.bottom + 1;
    })()`);
    const shortMobileDialog = await evaluate(client, `(() => {
      const dialog = document.querySelector("#intervention-dialog").getBoundingClientRect();
      const body = document.querySelector(".dialog-body").getBoundingClientRect();
      const footer = document.querySelector(".dialog-footer").getBoundingClientRect();
      const acknowledgement = document.querySelector("#simulation-acknowledgement").getBoundingClientRect();
      const acknowledgementRow = document.querySelector(".check-row").getBoundingClientRect();
      const visibleControl = document.querySelector(".custom-check").getBoundingClientRect();
      const viewportHeight = window.visualViewport?.height ?? window.innerHeight;
      return {
        dialogBottom: Math.round(dialog.bottom),
        viewportHeight: Math.round(viewportHeight),
        bodyBottom: Math.round(body.bottom),
        footerTop: Math.round(footer.top),
        acknowledgementTop: Math.round(acknowledgement.top),
        acknowledgementBottom: Math.round(acknowledgement.bottom),
        rowTop: Math.round(acknowledgementRow.top),
        rowBottom: Math.round(acknowledgementRow.bottom),
        visibleControlTop: Math.round(visibleControl.top),
        visibleControlBottom: Math.round(visibleControl.bottom),
        acknowledgementVisible:
          acknowledgement.top >= body.top - 1 && acknowledgement.bottom <= body.bottom + 1 &&
          acknowledgementRow.top >= body.top - 1 && acknowledgementRow.bottom <= body.bottom + 1 &&
          visibleControl.top >= body.top - 1 && visibleControl.bottom <= body.bottom + 1,
        focused: document.activeElement?.id
      };
    })()`);
    record(
      "short mobile viewport keeps focused fields above dialog actions",
      shortMobileDialog.dialogBottom <= shortMobileDialog.viewportHeight + 1 &&
        shortMobileDialog.bodyBottom <= shortMobileDialog.footerTop + 1 &&
        shortMobileDialog.acknowledgementVisible &&
        shortMobileDialog.focused === "simulation-acknowledgement",
      shortMobileDialog
    );
    await pressKey(client, "Escape", "Escape");
    await waitForExpression(client, `document.querySelector("#intervention-dialog").open === false`);
    await client.send("Emulation.setDeviceMetricsOverride", {
      width: 390,
      height: 844,
      deviceScaleFactor: 1,
      mobile: true
    });
    if (options.artifacts) await capture(client, resolve(ROOT, options.artifacts, "03-mobile-run.png"));

    await navigate(client, `${baseUrl}/app/?mode=empty#run`);
    await waitForExpression(client, `document.querySelector("#empty-state").hidden === false`);
    record("empty trace state is explicit", await evaluate(client, `document.querySelector("#empty-state h1").textContent === "No run in this trace"`));

    await navigate(client, `${baseUrl}/app/?mode=error#run`);
    await waitForExpression(client, `document.querySelector("#error-state").hidden === false`);
    const errorEvidence = await evaluate(client, `({
      issues: document.querySelectorAll("#error-list li").length,
      focused: document.activeElement === document.querySelector("#error-state h1")
    })`);
    record("invalid trace fails closed with evidence", errorEvidence.issues >= 1, errorEvidence);
    record("failed trace load moves focus to the error heading", errorEvidence.focused, errorEvidence);

    await navigate(client, `${baseUrl}/app/#run`);
    await waitForExpression(client, `document.body.dataset.runStatus === "blocked"`);
    await evaluate(client, `(async () => {
      const fixture = await fetch("./data/demo-run.json", { cache: "no-store" }).then((response) => response.json());
      fixture.run.agent.untrusted_override = true;
      const transfer = new DataTransfer();
      transfer.items.add(new File([JSON.stringify(fixture)], "schema-invalid.json", { type: "application/json" }));
      const input = document.querySelector("#trace-file");
      input.files = transfer.files;
      input.dispatchEvent(new Event("change", { bubbles: true }));
    })()`);
    await waitForExpression(client, `document.querySelector("#error-state").hidden === false`);
    const rejectedImport = await evaluate(client, `(() => ({
      issue: [...document.querySelectorAll("#error-list li")].find((item) => item.textContent.includes("run.agent.untrusted_override"))?.textContent ?? null,
      focused: document.activeElement === document.querySelector("#error-state h1"),
      renderedPanels: document.querySelectorAll("[data-view-panel]:not([hidden])").length
    }))()`);
    record(
      "schema-invalid local import fails closed before rendering",
      rejectedImport.issue?.includes("is not allowed") && rejectedImport.renderedPanels === 0,
      rejectedImport
    );
    record("rejected local import keeps focus on its error evidence", rejectedImport.focused, rejectedImport);

    const nonLocalRequests = requestedUrls.filter((url) => {
      try {
        return new URL(url).origin !== baseUrl;
      } catch {
        return false;
      }
    });
    record("browser journey makes no external network request", nonLocalRequests.length === 0, nonLocalRequests);
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
    await delay(120);
    rmSync(profile, { recursive: true, force: true });
  }

  const failed = checks.filter((check) => check.status === "fail");
  const report = {
    generated_at: new Date().toISOString(),
    surface: "Kyn.ist Flight Recorder standalone browser journey",
    runtime: {
      chromium: findChromium(),
      server: localTarget ? "Python standard library" : "external static origin",
      base_url: baseUrl,
      viewport_matrix: ["1440x1000", "390x844", "390x520 dialog"]
    },
    summary: {
      checks: checks.length,
      passed: checks.length - failed.length,
      failed: failed.length
    },
    metrics,
    checks,
    diagnostics: {
      server_output: serverOutput.join("").trim().split("\n").slice(-8),
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
