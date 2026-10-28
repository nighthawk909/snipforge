# Accessibility

SnipForge can be used from the keyboard: taking a snip, marking it up, cropping, saving and sharing
it, the Library and Settings. A few things follow the pointer and need a mouse, pen or touch: drawing
freehand, Smart Move, Cut out and choosing Edit text's line (see the editor below). Every control has
a name a screen reader (Narrator, NVDA, JAWS; VoiceOver on a Mac) can read, and SnipForge follows
the system's high-contrast setting. This page lists the keys and what to expect, for people who use
them and for IT reviews.

On a Mac, Ctrl in the keys below is ⌘.

## Seeing where the keyboard is

Whatever has the keyboard gets a border: in the accent color, or in a color that stands out from it
on controls already drawn in the accent (a primary button, a chosen option). That's buttons, tools,
switches, check boxes, lists and the Library card you're on.

## High contrast

With a Windows contrast theme on (Settings > Accessibility > Contrast themes), SnipForge draws its
windows in that theme's colors: its background, text and highlight. What's selected, checked or
focused is marked with the highlight color and a border, not with shades of gray. If a theme's text
can't be read on its background, SnipForge uses black and white instead. On a Mac, Increase contrast
(System Settings > Accessibility > Display) switches SnipForge to black and white (macOS has no
contrast colors for apps to read). While it's on, Settings › Appearance says so, the editor hides its
light/dark switch, and your light/dark and accent choices come back when it's off.

Your snips and the markup on them keep their own colors: they're your content.

## The editor

| Keys | What they do |
|---|---|
| F6, Shift+F6 | Go to the next (previous) part of the window: the snip, the properties panel, the command bar, the tools |
| Tab, Shift+Tab | Move through the controls of the part you're in. On the snip, select the markup one at a time; past the last, go on to the next part |
| Arrow keys, Home, End | In the tools: move between tools (and the Customize tools button under them). Enter or Space picks one and goes to the snip; Esc goes back without picking. Shift+F10 or the Menu key opens a tool's menu: favorite, hide, labels, Customize tools |
| Ctrl+K | Find a tool or action by name: type a few letters, Up and Down move through the matches, Enter runs one, Esc closes. Hidden tools are found too |
| V, A, L, R, E, S, M, T, K, I, N, P, H, B, X, W, C, U, G | Pick a tool (they're shown in each tool's tip) |
| Enter | With a drawing tool: add its markup in the middle of the snip you can see, selected. With text selected: edit it (F2 too) |
| Arrow keys | Move what's selected by a pixel (Shift: 10). While cropping (C), move the crop |
| Ctrl+arrow keys | Make what's selected wider, narrower, taller or shorter by a pixel (Shift: 10); stamps stay square. While cropping, size the crop. Presses in a row are one undo step |
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
| Alt+Up, Alt+Down | In the Layers list: move the chosen markup over the one above it, under the one below it. Tab goes on to each row's eye (hide) and lock |

The pen, highlighter, Smart Move and Cut out follow the pointer, so they need a mouse, pen or touch
(Enter with one of them says so). Edit text's line is chosen by clicking it; its new words are typed.
Crop from the keyboard: C, then the arrow keys and Ctrl+arrow keys, then Enter.

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
| Arrow keys | In Window, Area and Scrolling modes: go from window to window (Area and Scrolling end with the whole monitor). In Fixed size: move the frame (Shift: by 10) |
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

`tests/test_accessibility.py` checks the controls of the editor (with every tool's panel), the
Library, Settings (every page) and the capture overlay for a name; the keyboard paths above with
real key presses; that the colors of two Windows contrast themes keep their text and highlights at
7:1 or better (WCAG AAA); and, by comparing pictures, that focus shows on every kind of control in
light, dark and both contrast themes.
