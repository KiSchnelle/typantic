// The dashboard's brand, as the page shows it: the accent's shades and the tab's
// icon. The name and the sidebar mark render from the store (see App.tsx).

export type BrandShade = "300" | "400" | "500" | "600" | "700" | "950";

// How each shade is made from a brand's accent, which is the 400 step: mixes in
// OKLab, fitted to Tailwind's cyan ramp, so an accent gets shades as evenly
// spaced as the ones index.css holds for typantic's own cyan.
export function accentShades(accent: string): Record<BrandShade, string> {
  return {
    "300": `color-mix(in oklab, ${accent} 72%, white)`,
    "400": accent,
    "500": `color-mix(in oklab, ${accent} 90%, black)`,
    "600": `color-mix(in oklab, ${accent} 77%, black)`,
    "700": `color-mix(in oklab, ${accent} 65%, black)`,
    "950": `color-mix(in oklab, ${accent} 38%, black)`,
  };
}

// Recolour the dashboard: every brand-* utility and accent rule reads these
// variables. Without an accent, index.css's defaults (typantic's cyan) stand.
export function applyAccent(accent: string | null): void {
  if (!accent) return;
  const root = document.documentElement;
  for (const [shade, value] of Object.entries(accentShades(accent))) {
    root.style.setProperty(`--color-brand-${shade}`, value);
  }
}

// Show a brand's mark as the tab's icon. index.html links typantic's, as an SVG
// and as a PNG for browsers without SVG tab icons; the brand replaces the SVG
// one and drops the PNG, which such a browser would still pick.
export function showFavicon(icon: string | null): void {
  if (!icon) return;
  const svg = document.querySelector<HTMLLinkElement>(
    'link[rel="icon"][type="image/svg+xml"]',
  );
  if (svg) svg.href = icon;
  document.querySelector('link[rel="icon"][type="image/png"]')?.remove();
}
