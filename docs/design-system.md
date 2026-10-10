# Waterline design system ("Hybrid")

One system for every page: the main site, preview, blog, admin and all of
Collections. Chosen from four directions (see the comparison artifact from
2026-10-10). In short: a glow hero on marketing pages, calm neutral tools,
and motion only where it explains a state change.

## Files

| File | What it holds |
| --- | --- |
| `assets/tokens.css` | Colours, type, radius, motion tokens; the `.glow` hero; motion primitives (`.press`, `.tick`, `.toast-spring`); the waterline meter (`.tank`). **Loaded first on every page.** |
| `assets/theme.css` | Main site, blog and admin components (nav, buttons, cards, chips…), built on the tokens. |
| `collections/assets/cl.css` | Collections components, built on the same tokens (with short aliases such as `--dim`). |
| `assets/motion.js` | `WLMotion.countUp / countAll / fill / tick`; honours reduced motion. |

## Rules

- **Colour.** Neutrals with a slight green bias. One brand accent (deep
  green `--accent`) for primary actions. Water blue (`--water`) for data,
  progress and the waterline meter. Semantic red, amber and green only for
  state.
- **Type.**
  - Geist for everything, and Geist Mono for figures and labels.
  - Bricolage Grotesque only for headlines on marketing pages: add
    `class="marketing"` to `<body>` and use `.glow` heroes.
  - Tools (admin, ops, client portal) never use Bricolage.
- **Fonts per page.**
  - Marketing pages load Bricolage, Geist and Geist Mono.
  - Tools load Geist and Geist Mono.
  - The debtor payment page loads Geist (400/600/700) only, to stay well
    under its 200 KB budget.
- **Glow.** Only on marketing heroes and the pricing panel. Never in tools,
  and never on the debtor page.
- **Motion.**
  - Allowed for: count-ups on dashboard figures, the confirm → tick
    morph, toasts, button press, and the waterline meter.
  - Nothing animates in a way that hides content at rest.
  - `prefers-reduced-motion` turns all of it off.
- **New pages.** Link the right font set, then `tokens.css`, then
  `theme.css` (or `cl.css`). Use tokens, never literal colours.
- **Not done yet.** The tokens are structured for a dark mode in the
  tools, but it isn't switched on yet.
