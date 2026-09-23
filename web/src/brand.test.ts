// A brand's accent reaches every shade the dashboard uses; typantic's own cyan
// needs nothing at runtime (index.css holds it).

import { afterEach, expect, test } from "vitest";
import { accentShades, applyAccent, showFavicon } from "./brand.ts";

afterEach(() => {
  document.documentElement.removeAttribute("style");
  document.head.innerHTML = "";
});

test("an accent becomes all six shades, the accent itself as 400", () => {
  expect(accentShades("#5AA9FF")).toEqual({
    "300": "color-mix(in oklab, #5AA9FF 72%, white)",
    "400": "#5AA9FF",
    "500": "color-mix(in oklab, #5AA9FF 90%, black)",
    "600": "color-mix(in oklab, #5AA9FF 77%, black)",
    "700": "color-mix(in oklab, #5AA9FF 65%, black)",
    "950": "color-mix(in oklab, #5AA9FF 38%, black)",
  });
});

test("applying an accent sets the theme's variables", () => {
  applyAccent("#5AA9FF");
  const style = document.documentElement.style;
  expect(style.getPropertyValue("--color-brand-400")).toBe("#5AA9FF");
  expect(style.getPropertyValue("--color-brand-950")).toBe(
    "color-mix(in oklab, #5AA9FF 38%, black)",
  );
});

test("no accent leaves the stylesheet's cyan alone", () => {
  applyAccent(null);
  expect(document.documentElement.style.getPropertyValue("--color-brand-400")).toBe("");
});

function iconLinks(): void {
  document.head.innerHTML =
    '<link rel="icon" type="image/svg+xml" href="/favicon.svg">' +
    '<link rel="icon" type="image/png" sizes="32x32" href="/icon-32.png">';
}

test("a brand's mark replaces typantic's in the tab", () => {
  // The PNG goes too: a browser without SVG tab icons would still pick it.
  iconLinks();
  showFavicon("data:image/svg+xml;base64,PHN2Zy8+");
  const links = document.head.querySelectorAll<HTMLLinkElement>('link[rel="icon"]');
  expect([...links].map((link) => link.getAttribute("href"))).toEqual([
    "data:image/svg+xml;base64,PHN2Zy8+",
  ]);
});

test("without a mark typantic's stays", () => {
  iconLinks();
  showFavicon(null);
  expect(document.head.querySelectorAll('link[rel="icon"]')).toHaveLength(2);
});
