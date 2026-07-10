#!/usr/bin/env node
// Browser regression for the generated TODO Markdown renderer.
// Usage: node tests/test_markdown_rendering.mjs /path/to/generated/todos.html
import fs from "node:fs";

const playwright = await import(process.env.PLAYWRIGHT_PATH || "playwright");
const { chromium, webkit } = playwright;

const uiPath = process.argv[2];
if (!uiPath) throw new Error("usage: test_markdown_rendering.mjs /path/to/todos.html");
const ui = fs.readFileSync(uiPath, "utf8");
const start = ui.indexOf("function safeMarkdownHref");
const end = ui.indexOf("function evEl", start);
if (start < 0 || end < 0) throw new Error("Markdown renderer not found in generated UI");
const renderer = ui.slice(start, end);
const source = [
  "# Heading",
  "",
  "**bold** *italic* `inline`",
  "plain line one",
  "plain line two",
  "",
  "- unordered",
  "",
  "1. ordered",
  "",
  "> quote",
  "",
  "[safe](https://example.com)",
  "",
  "```js",
  "const value = '<script>';",
  "```",
  "",
  "<script>window.__markdownXss=1</script>",
  "<img src=x onerror=\"window.__markdownXss=1\">",
  "[bad](javascript:window.__markdownXss=1)",
].join("\n");

for (const [name, launcher] of [["chromium", chromium], ["webkit", webkit]]) {
  const browser = await launcher.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.setContent(`<div id="out"></div><script>${renderer}<\/script>`);
    await page.evaluate(value => renderMarkdown(document.querySelector("#out"), value), source);
    const out = page.locator("#out");
    for (const selector of ["h1", "strong", "em", "p br", "ul li", "ol li", "blockquote", "pre code", 'a[href="https://example.com"]']) {
      if (await out.locator(selector).count() < 1) throw new Error(`${name}: missing ${selector}`);
    }
    if (await out.locator("script,img,iframe,object,embed").count()) throw new Error(`${name}: unsafe HTML rendered`);
    if (await out.locator('a[href^="javascript:"],a[href^="data:"]').count()) throw new Error(`${name}: unsafe link rendered`);
    const safeLink = out.locator('a[href="https://example.com"]');
    if (await safeLink.getAttribute("target") !== "_blank") throw new Error(`${name}: safe link missing target=_blank`);
    const rel = (await safeLink.getAttribute("rel") || "").split(/\s+/);
    if (!rel.includes("noopener") || !rel.includes("noreferrer")) throw new Error(`${name}: safe link missing safe rel`);
    const popupPromise = page.waitForEvent("popup");
    await safeLink.click();
    const popup = await popupPromise;
    await popup.waitForLoadState("domcontentloaded");
    if (new URL(popup.url()).hostname !== "example.com") throw new Error(`${name}: bad popup ${popup.url()}`);
    await popup.close();
    if (await page.evaluate(() => Boolean(window.__markdownXss))) throw new Error(`${name}: injected code executed`);
    const text = await out.innerText();
    if (!text.includes("<script>window.__markdownXss=1</script>") || !text.includes("<img src=x onerror=")) {
      throw new Error(`${name}: unsafe raw HTML was not preserved as text`);
    }
  } finally {
    await browser.close();
  }
}
console.log("PASS safe Markdown rendering in Chromium + WebKit");
