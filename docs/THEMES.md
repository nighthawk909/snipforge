# Themes and theme packs

SnipForge draws every window from one set of design tokens (`snipforge/theme/tokens.py`), so
light, dark and accent colors change everywhere at once.

- **Light, dark or match Windows.** SnipForge follows the Windows setting by default and switches
  live when it changes. The choice is stored as `appearance/theme` (`system`, `light`, `dark`).
- **Accent.** Built in: SnipForge blue, Teal and Graphite. Stored as `appearance/accent`.
- **Title bars** are real Windows title bars, colored to match through the Windows 11 DWM API.
  On older Windows they stay standard.

## Theme packs

A theme pack adds an accent and, optionally, a colored header band. Organizations use one to match
their brand without changing code. A pack is a small JSON file:

```json
{
  "schema": 1,
  "id": "acme",
  "name": "Acme",
  "default": true,
  "accent": {
    "light": {"fill": "#461E96", "fill_hover": "#3A1880", "on": "#FFFFFF", "text": "#461E96", "soft": "#EEE9FA"},
    "dark":  {"fill": "#5B3CC4", "fill_hover": "#6B4FD0", "on": "#FFFFFF", "text": "#B6A9F2", "soft": "#2F2754"}
  },
  "band": {"bg": "#211359", "text": "#FFFFFF", "text2": "#CBC4EE", "primary_bg": "#FFFFFF", "primary_text": "#211359"}
}
```

| Field | Meaning |
|---|---|
| `fill` / `fill_hover` | Primary buttons and checked controls |
| `on` | Text on `fill` |
| `text` | Accent-colored text and icons on normal backgrounds |
| `soft` | Background of selected tools, chips and tags |
| `band` | Optional header band (command bars and title bars) |
| `default` | Use this accent until the person picks another one |

SnipForge validates every pack and skips a bad one with a log message; it never stops the app:

- colors must be plain `#RRGGBB`
- text must have at least 4.5:1 contrast with what it sits on (WCAG AA), in both light and dark
- `id` is 1-32 lowercase letters, digits or dashes and can't reuse a built-in id
- files over 64 KB are ignored

### Where packs are loaded from

1. `themes\` next to `SnipForge.exe`, for organization builds. The installer puts it there.
2. `%LOCALAPPDATA%\SnipForge\themes\` for a person's own packs.

The first pack with a given `id` wins.

### Shipping a pack in an organization build

Put the pack in `themes/<id>.json` at the repository root and build as usual
(`python scripts/build.py --installer`). The build validates it (a bad pack fails the build) and
copies it next to `SnipForge.exe`. The public repository has no `themes/` folder, so its builds
ship only the built-in accents. Keep company branding out of the public repository.
