# typantic logo

Colors: cyan #22d3ee (on dark), cyan #0891b2 (on light), ink #0b0f14, text #e6edf5.
Wordmark: Instrument Sans Bold, letter-spacing -0.04em (font embedded in logo-*.svg).

- mark.svg: primary mark (for dark backgrounds)
- mark-on-light.svg: mark for light backgrounds
- mark-mono-black.svg / mark-mono-white.svg: one-color, with a transparent cutout
- logo-dark.svg / logo-light.svg: horizontal lockups
- favicon.svg + png/icon-{16,32,48,512}.png, png/apple-touch-icon-180.png
- png/readme-banner.png: 1280×320 header

README usage:

```html
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/img/logo-dark.svg">
  <img alt="typantic" src="docs/img/logo-light.svg" height="72">
</picture>
```
