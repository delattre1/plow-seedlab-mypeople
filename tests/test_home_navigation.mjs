#!/usr/bin/env node
// Browser regression for Home navigation to the existing HUD and Terminal Graph surfaces.
// Usage: node tests/test_home_navigation.mjs http://127.0.0.1:9933 [other-base-url ...]
//        node tests/test_home_navigation.mjs --graph-only https://public.example/terminal-graph
const { chromium, webkit } = await import(process.env.PLAYWRIGHT_PATH || "playwright");

const args = process.argv.slice(2);
const graphOnly = args[0] === "--graph-only";
const bases = graphOnly ? args.slice(1) : args;
const httpPassword = process.env.MYPEOPLE_HTTP_PASSWORD || "";
const proofPath = process.env.MYPEOPLE_PROOF_PATH || "";
const graphSettleMs = Number(process.env.MYPEOPLE_GRAPH_SETTLE_MS || 3000);
if (!bases.length) throw new Error("usage: test_home_navigation.mjs [--graph-only] <url> [url ...]");

for (const [engine, launcher] of [["chromium", chromium], ["webkit", webkit]]) {
  const browser = await launcher.launch({ headless: true });
  try {
    for (const base of bases) {
      if (graphOnly && httpPassword) {
        const anonymous = await browser.newPage({ viewport: { width: 1280, height: 720 } });
        const denied = await anonymous.goto(base, { waitUntil: "domcontentloaded" });
        if (!denied || denied.status() !== 401) throw new Error(`${engine} ${base}: anonymous Graph was not denied`);
        if (denied.headers()["set-cookie"]) throw new Error(`${engine} ${base}: anonymous Graph minted a session cookie`);
        if ((await anonymous.context().cookies()).some(cookie => cookie.name === "mp_session")) {
          throw new Error(`${engine} ${base}: anonymous browser received mp_session`);
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
        await page.waitForFunction(() => !document.querySelector("#counts")?.textContent?.includes("connecting"));
        const counts = await page.locator("#counts").innerText();
        if (!/\d+ terminals.*\d+ shared tasks/i.test(counts)) throw new Error(`${engine} ${base}: Graph data did not load (${counts})`);
        await page.waitForFunction(() => document.querySelectorAll(".node iframe").length > 0);
        await page.waitForTimeout(graphSettleMs);
        const iframeSources = await page.locator(".node iframe").evaluateAll(frames => frames.map(frame => frame.src));
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

      await page.goto(base, { waitUntil: "domcontentloaded" });
      const hudNavigation = page.waitForNavigation({ waitUntil: "domcontentloaded" });
      await page.locator(".brand .subt").getByRole("link", { name: "HUD ↗", exact: true }).click();
      const hudResponse = await hudNavigation;
      if (!hudResponse || hudResponse.status() !== 200 || new URL(page.url()).pathname !== "/dashboard") {
        throw new Error(`${engine} ${base}: HUD navigation regressed`);
      }
      await page.close();
    }
  } finally {
    await browser.close();
  }
}

console.log("PASS Home navigation to HUD + Terminal Graph in Chromium + WebKit");
