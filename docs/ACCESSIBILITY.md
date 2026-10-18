# Accessibility

SnipForge can be used from the keyboard alone, every control has a name a screen reader (Narrator,
NVDA, JAWS; VoiceOver on a Mac) can read, and it follows the system's high-contrast setting. This
page lists the keys and what to expect, for people who use them and for IT reviews.

On a Mac, Ctrl in the keys below is ⌘.

## Seeing where the keyboard is

Whatever has the keyboard gets a border in the accent color: buttons, tools, switches, lists and the
Library card you're on.

## High contrast

With a Windows contrast theme on (Settings > Accessibility > Contrast themes), SnipForge draws its
windows in that theme's colors: its background, text and highlight. What's selected, checked or
focused is marked with the highlight color and a border, not with shades of gray. On a Mac, Increase
contrast (System Settings > Accessibility > Display) gives the same black-and-white look. While it's
on, Settings › Appearance says so, and the light/dark and accent choices come back when it's off.

Your snips and the markup on them keep their own colors: they're your content.

## The editor

| Keys | What they do |
|---|---|
| F6, Shift+F6 | Go to the next (previous) part of the window: the snip, the properties panel, the command bar, the tools |
| Tab, Shift+Tab | Move through the controls of the part you're in. On the snip, select the markup one at a time; past the last, go on to the next part |
| Arrow keys, Home, End | In the tools: move between tools. Enter or Space picks one and goes to the snip; Esc goes back without picking |
| V, A, L, R, E, S, M, T, K, I, N, P, H, B, X, W, C, U, G | Pick a tool (they're shown in each tool's tip) |
| Enter | With a drawing tool: add its markup in the middle of the snip you can see, selected. With text selected: edit it (F2 too) |
| Arrow keys | Move what's selected by a pixel (Shift: 10) |
| Ctrl+arrow keys | Make what's selected wider, narrower, taller or shorter by a pixel (Shift: 10) |
| Delete | Remove what's selected |
| Esc | Stop typing, deselect, then go back to Select |
| Ctrl+A, Ctrl+D | Select all markup; duplicate what's selected |
| Ctrl+], Ctrl+[ | Bring what's selected forward, send it back |
| Ctrl+C, Ctrl+X, Ctrl+V | Copy the snip (with the selected markup, for pasting), cut and paste markup |
| Ctrl+Z, Ctrl+Y | Undo, redo |
| Ctrl+S, Ctrl+Shift+S | Save, Save as |
| Ctrl+R, Ctrl+Shift+R | Turn the snip right, left |
| Ctrl+Shift+T, Ctrl+Shift+E | Grab text, Edit text |
| Ctrl+P, Ctrl+E, Ctrl+G | Pin, email, combine |
| Ctrl+=, Ctrl+-, Ctrl+0, Ctrl+1 | Zoom in, out, fit, 100% |
| Space (held) | Pan the snip with the mouse |

The pen, highlighter, Smart Move and Cut out follow the pointer, so they need a mouse, pen or touch
(Enter with one of them says so). Edit text's line is chosen by clicking it; its new words are typed.

## The Library

| Keys | What they do |
|---|---|
| Arrow keys, Home, End, Page Up, Page Down | Move between snips (Shift or Ctrl to select several) |
| Enter | Open the snip |
| Menu key or Shift+F10 | The snip's menu (open, combine, copy, show in folder, favorite, tags, delete), beside it |
| F, T | Favorite, tags |
| Ctrl+C | Copy the snip |
| Delete | Move to the Recycle Bin |
| Ctrl+G | Combine the selected snips |
| Ctrl+F | Search |
| F5 | Reload |

## Taking a snip

| Keys | What they do |
|---|---|
| A, W, F, X, S | Area, Window, Full screen, Fixed size, Scrolling |
| Arrow keys | In Window and Area modes: go from window to window (Area mode ends with the whole monitor). In Fixed size: move the frame (Shift: by 10) |
| Enter | Snip the window you chose with the arrow keys, the Fixed size frame, or else the whole monitor |
| Tab | Go round the capture toolbar |
| D, P, R | Delay, include the pointer, record |
| 1 to 9, 0 | A capture preset; your usual choices |
| Esc | Cancel |

## Video editor

| Keys | What they do |
|---|---|
| Space | Play or pause |
| Left, Right | A frame back or forward (Shift: further), on the playhead or the trim handle you're on |
| Home, End | To the start or end of what's kept |
| I, O | Set where the video starts and ends |

## Screen readers

Every button, tool, switch, slider, list and text box has a name, and icon-only buttons are named
for what they do. The snip itself is "Snip", described with the keys above. SnipForge uses Qt's
accessibility support, which talks to Windows UI Automation and to the Mac's accessibility API.

## Checked how

`tests/test_accessibility.py` checks every window's controls for a name, the keyboard paths above
with real key presses, the contrast colors (at least 7:1, WCAG AAA) and that focus is drawn.
