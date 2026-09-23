# typantic logo

Colors: cyan #22d3ee (on dark), cyan #0891b2 (on light), ink #0b0f14, text #e6edf5.
Wordmark: Instrument Sans Bold, letter-spacing -0.04em. logo-*.svg name the font
but do not embed it, so where it is not installed (GitHub, PyPI) the wordmark
falls back to a generic sans-serif; the PNG lockups under png/ carry it drawn.

- mark.svg: primary mark (for dark backgrounds)
- mark-on-light.svg: mark for light backgrounds
- mark-mono-black.svg / mark-mono-white.svg: one-color, with a transparent cutout
- logo-dark.svg / logo-light.svg: horizontal lockups
- favicon.svg + png/icon-{16,32,48,512}.png, png/apple-touch-icon-180.png
- png/readme-banner.png: 1280×320 header

README usage (the PNGs, for the font; on PyPI use absolute
`https://raw.githubusercontent.com/KiSchnelle/typantic/main/logo/png/...` URLs,
as relative ones only resolve on GitHub):

```html
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="logo/png/logo-dark-2x.png">
  <img alt="typantic" src="logo/png/logo-light-2x.png" height="72">
</picture>
```
