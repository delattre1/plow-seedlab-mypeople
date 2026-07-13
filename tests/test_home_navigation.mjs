#!/usr/bin/env node
// Browser regression for Home navigation to the existing HUD and Terminal Graph surfaces.
// Usage: node tests/test_home_navigation.mjs http://127.0.0.1:9933 [other-base-url ...]
//        node tests/test_home_navigation.mjs --graph-only https://public.example/terminal-graph
const { chromium, webkit } = await import(process.env.PLAYWRIGHT_PATH || "playwright");
import { execFileSync } from "node:child_process";

const args = process.argv.slice(2);
const graphOnly = args[0] === "--graph-only";
const bases = graphOnly ? args.slice(1) : args;
const httpPassword = process.env.MYPEOPLE_HTTP_PASSWORD || "";
const proofPath = process.env.MYPEOPLE_PROOF_PATH || "";
const graphSettleMs = Number(process.env.MYPEOPLE_GRAPH_SETTLE_MS || 30000);
const skipVisualAssert = process.env.MYPEOPLE_SKIP_VISUAL_ASSERT === "1";
if (!bases.length) throw new Error("usage: test_home_navigation.mjs [--graph-only] <url> [url ...]");

function localTmuxCounts() {
  try {
    const output = execFileSync("tmux", ["list-windows", "-a", "-F",
      "#{session_name}\t#{window_name}\t#{pane_dead}"], {
      encoding: "utf8", env: { ...process.env, TMUX: "" }, timeout: 5000,
    });
    const windows = output.trim().split("\n").filter(Boolean).map(line => line.split("\t"))
      .filter(([session, window, dead]) => {
        if (!session.startsWith("mc-") || dead === "1") return false;
        const sid = session.slice(3).toLowerCase();
        const win = window.toLowerCase();
        return !sid.startsWith("_v") && !sid.startsWith("test") && !sid.startsWith("verify") &&
          !win.startsWith("_v") && !win.startsWith("test-") && !win.startsWith("verify-") &&
          !win.startsWith("graph-test");
      });
    return { total: windows.length, main: windows.filter(([session]) => session === "mc-main").length };
  } catch (_) {
    return null;
  }
}

async function assertWallRemoved(browser, engine, base, credentials) {
  const page = await browser.newPage({
    viewport: { width: 320, height: 720 },
    ...(credentials ? { httpCredentials: credentials } : {}),
  });
  try {
    const origin = new URL(base).origin;
    for (const path of ["/wall", "/todo/wall"]) {
      const response = await page.goto(origin + path, { waitUntil: "domcontentloaded" });
      if (!response || response.status() !== 404) {
        throw new Error(`${engine} ${origin}${path}: removed Wall route did not return 404`);
      }
      if (response.headers()["set-cookie"]) {
        throw new Error(`${engine} ${origin}${path}: removed Wall route minted a cookie`);
      }
      if ((await page.context().cookies()).some(cookie => cookie.name === "mp_session")) {
        throw new Error(`${engine} ${origin}${path}: removed Wall route minted mp_session`);
      }
    }
  } finally {
    await page.close();
  }
}

for (const [engine, launcher] of [["chromium", chromium], ["webkit", webkit]]) {
  const browser = await launcher.launch({ headless: true });
  try {
    for (const base of bases) {
      const credentials = graphOnly && httpPassword
        ? { username: "mypeople", password: httpPassword }
        : null;
      await assertWallRemoved(browser, engine, base, credentials);

      if (graphOnly && httpPassword) {
        const anonymous = await browser.newPage({ viewport: { width: 1280, height: 720 } });
        const origin = new URL(base).origin;
        for (const path of ["/terminal-graph", "/todo/terminal-graph", "/todo/board"]) {
          const denied = await anonymous.goto(origin + path, { waitUntil: "domcontentloaded" });
          if (!denied || denied.status() !== 401) throw new Error(`${engine} ${origin}${path}: anonymous protected route was not denied`);
          if (denied.headers()["set-cookie"]) throw new Error(`${engine} ${origin}${path}: anonymous protected route minted a session cookie`);
          if ((await anonymous.context().cookies()).some(cookie => cookie.name === "mp_session")) {
            throw new Error(`${engine} ${origin}${path}: anonymous browser received mp_session`);
          }
        }
        await anonymous.close();
      }
      const page = await browser.newPage({
        viewport: graphOnly && proofPath && engine === "chromium" ? { width: 1280, height: 720 } : { width: 320, height: 720 },
        ...(graphOnly && httpPassword ? { httpCredentials: { username: "mypeople", password: httpPassword } } : {}),
      });
      const terminalSockets = [];
      page.on("websocket", socket => {
        const item = { url: socket.url(), frames: 0, closed: false };
        terminalSockets.push(item);
        socket.on("framereceived", () => item.frames++);
        socket.on("close", () => item.closed = true);
      });
      const response = await page.goto(base, { waitUntil: "domcontentloaded" });
      if (!response || response.status() !== 200) throw new Error(`${engine} ${base}: page did not return 200`);

      if (graphOnly) {
        if (new URL(page.url()).pathname !== "/terminal-graph") throw new Error(`${engine} ${base}: wrong Terminal Graph URL`);
        if (await page.title() !== "MyPeople · Terminal Graph") throw new Error(`${engine} ${base}: existing Terminal Graph did not render`);
        if (await page.locator('a[href="/wall"]').count()) throw new Error(`${engine} ${base}: Terminal Graph still links to Wall`);
        await page.waitForFunction(() => !document.querySelector("#counts")?.textContent?.includes("connecting"));
        const initialCounts = await page.locator("#counts").innerText();
        if (!/\d+ terminals.*\d+ shared tasks/i.test(initialCounts)) throw new Error(`${engine} ${base}: Graph data did not load (${initialCounts})`);
        await page.waitForFunction(() => document.querySelectorAll(".node iframe").length > 0);
        await page.waitForTimeout(graphSettleMs);
        const settledCounts = await page.locator("#counts").innerText();
        const headerCount = Number(settledCounts.match(/(\d+) terminals/i)?.[1]);
        const iframeSources = await page.locator(".node iframe").evaluateAll(frames => frames.map(frame => frame.src));
        if (headerCount !== iframeSources.length) {
          throw new Error(`${engine} ${base}: header says ${headerCount} terminals but Graph rendered ${iframeSources.length}`);
        }
        if (["127.0.0.1", "localhost"].includes(new URL(page.url()).hostname)) {
          const expected = localTmuxCounts();
          const graphTargets = await page.evaluate(() => graph.nodes.map(node => node.target));
          const mainCount = graphTargets.filter(target => target.startsWith("mc-main:")).length;
          if (expected && (headerCount !== expected.total || mainCount !== expected.main)) {
            throw new Error(`${engine} ${base}: Graph/header under-counted live tmux windows ` +
              `(header=${headerCount}, graph-main=${mainCount}, tmux-total=${expected.total}, tmux-main=${expected.main})`);
          }
        }
        const isHttps = new URL(page.url()).protocol === "https:";
        if (isHttps && iframeSources.some(src => new URL(src).pathname.indexOf("/ttyd-ro/") !== 0)) {
          throw new Error(`${engine} ${base}: HTTPS Graph did not use same-origin /ttyd-ro/`);
        }
        if (!isHttps && iframeSources.some(src => new URL(src).port !== "7682")) {
          throw new Error(`${engine} ${base}: direct Graph did not use read-only ttyd :7682`);
        }
        const streaming = terminalSockets.filter(socket => !socket.closed && socket.frames > 0 && /\/ws\?arg=/.test(socket.url));
        if (streaming.length !== iframeSources.length) {
          throw new Error(`${engine} ${base}: only ${streaming.length}/${iframeSources.length} terminal WebSockets stream`);
        }
        const reconnectFrames = [];
        for (const frame of page.frames().filter(frame => frame !== page.mainFrame() && !frame.url().startsWith("about:"))) {
          const text = await frame.locator("body").innerText({ timeout: 1000 }).catch(() => "");
          if (/Press\s*.*to Reconnect/i.test(text)) reconnectFrames.push(frame.url());
        }
        if (reconnectFrames.length) {
          throw new Error(`${engine} ${base}: ${reconnectFrames.length}/${iframeSources.length} terminal iframes show reconnect`);
        }
        if (!skipVisualAssert) {
          const bossSrc = await page.locator('.node[data-master="true"] iframe').getAttribute("src");
          const bossFrame = page.frames().find(frame => frame.url() === bossSrc);
          if (!bossFrame) throw new Error(`${engine} ${base}: Boss terminal iframe context is missing`);
          const bossBox = await page.evaluate(() => {
            const screen = document.querySelector('.node[data-master="true"] .screen');
            if (!screen) return null;
            const box = screen.getBoundingClientRect();
            return { x: box.x, y: box.y, width: box.width, height: box.height };
          });
          if (!bossBox || bossBox.width < 20 || bossBox.height < 20) {
            throw new Error(`${engine} ${base}: Boss xterm screen has no rendered area`);
          }
          // xterm's active renderer is WebGL, whose canvas buffer is intentionally not readable
          // after compositing. An untouched element screenshot is the browser's rendered truth.
          // A uniform black xterm PNG has very low bytes-per-rendered-pixel density; real terminal
          // glyphs add enough spatial entropy to remain comfortably above this conservative floor.
          const bossPng = await page.screenshot({ clip: bossBox });
          const bossPaintDensity = bossPng.length / (bossBox.width * bossBox.height);
          if (bossPaintDensity < 0.02) {
            throw new Error(`${engine} ${base}: Boss terminal stayed visually blank (PNG ${bossPng.length} bytes, density ${bossPaintDensity.toFixed(4)})`);
          }
        }
        const identitiesPersist = await page.evaluate(async () => {
          const before = [...document.querySelectorAll(".node iframe")];
          await new Promise(resolve => setTimeout(resolve, 2800));
          const after = [...document.querySelectorAll(".node iframe")];
          return before.length === after.length && after.every((frame, index) => frame === before[index]);
        });
        if (!identitiesPersist) throw new Error(`${engine} ${base}: metadata refresh recreated terminal iframes`);
        const interactive = await page.evaluate(() => ttyUrl(rwPort, graph.nodes[0].target));
        if (isHttps ? !interactive.startsWith("/ttyd-rw/") : new URL(interactive).port !== "7681") {
          throw new Error(`${engine} ${base}: interactive attach transport is wrong (${interactive})`);
        }
        if (proofPath && engine === "chromium") await page.screenshot({ path: proofPath });
        await page.close();
        continue;
      }

      if (await page.locator("h1").innerText() !== "Priorities") throw new Error(`${engine} ${base}: Priorities Home regressed`);
      if (await page.locator('a[href="/wall"]').count()) throw new Error(`${engine} ${base}: Priorities still links to Wall`);
      const nav = page.locator(".brand .subt");
      const hud = nav.getByRole("link", { name: "HUD ↗", exact: true });
      const graph = nav.getByRole("link", { name: "Terminal Graph ↗", exact: true });
      if (await hud.getAttribute("href") !== "/dashboard") throw new Error(`${engine} ${base}: HUD link changed`);
      if (await graph.getAttribute("href") !== "/terminal-graph") throw new Error(`${engine} ${base}: Terminal Graph link is not relative`);
      if (await graph.getAttribute("target")) throw new Error(`${engine} ${base}: Terminal Graph does not match HUD open behavior`);
      if (!await graph.isVisible()) throw new Error(`${engine} ${base}: Terminal Graph link is not visible at mobile width`);
      const box = await graph.boundingBox();
      if (!box || box.x < 0 || box.x + box.width > 320) throw new Error(`${engine} ${base}: Terminal Graph link overflows mobile viewport`);

      await graph.focus();
      if (!await graph.evaluate(el => el === document.activeElement)) throw new Error(`${engine} ${base}: Terminal Graph link is not keyboard reachable`);
      if (await graph.evaluate(el => getComputedStyle(el).outlineStyle === "none")) throw new Error(`${engine} ${base}: Terminal Graph focus is not visible`);

      const graphNavigation = page.waitForNavigation({ waitUntil: "domcontentloaded" });
      await page.keyboard.press("Enter");
      const graphResponse = await graphNavigation;
      if (!graphResponse || graphResponse.status() !== 200) throw new Error(`${engine} ${base}: Terminal Graph did not return 200`);
      if (new URL(page.url()).pathname !== "/terminal-graph") throw new Error(`${engine} ${base}: wrong Terminal Graph destination ${page.url()}`);
      if (await page.title() !== "MyPeople · Terminal Graph") throw new Error(`${engine} ${base}: existing Terminal Graph did not render`);
      if (await page.locator('a[href="/wall"]').count()) throw new Error(`${engine} ${base}: Terminal Graph still links to Wall`);

      await page.goto(base, { waitUntil: "domcontentloaded" });
      const hudNavigation = page.waitForNavigation({ waitUntil: "domcontentloaded" });
      await page.locator(".brand .subt").getByRole("link", { name: "HUD ↗", exact: true }).click();
      const hudResponse = await hudNavigation;
      if (!hudResponse || hudResponse.status() !== 200 || new URL(page.url()).pathname !== "/dashboard") {
        throw new Error(`${engine} ${base}: HUD navigation regressed`);
      }
      if (await page.title() !== "MyPeople - HUD") throw new Error(`${engine} ${base}: HUD page regressed`);
      if (await page.locator('a[href="/wall"]').count()) throw new Error(`${engine} ${base}: HUD still links to Wall`);
      await page.close();
    }
  } finally {
    await browser.close();
  }
}

console.log("PASS Home navigation to HUD + Terminal Graph in Chromium + WebKit");
