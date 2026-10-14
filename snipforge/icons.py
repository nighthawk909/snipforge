"""Line icons drawn as inline SVG (24x24, stroke-based) and tinted at runtime."""
from __future__ import annotations

from functools import cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_PATHS = {
    "select": '<path d="M5 3l14 8-6 1.5L10 19z"/>',
    "arrow": '<path d="M5 19L19 5M10 5h9v9"/>',
    "line": '<path d="M5 19L19 5"/>',
    "rect": '<rect x="4" y="6" width="16" height="12" rx="1.5"/>',
    "ellipse": '<ellipse cx="12" cy="12" rx="8" ry="6"/>',
    "pen": '<path d="M4 20l1-4L16 5l3 3L8 19z"/><path d="M14 7l3 3"/>',
    "highlight": '<path d="M9 14l-4 4v2h4l4-4"/><path d="M9 14l7-9 4 4-9 7z"/>',
    "text": '<path d="M5 6V4h14v2M12 4v16M9 20h6"/>',
    "step": '<circle cx="12" cy="12" r="8"/><path d="M11 9l2-1v8"/>',
    "pixelate": ('<rect x="4" y="4" width="5" height="5"/><rect x="15" y="4" width="5" height="5"/>'
                 '<rect x="9.5" y="9.5" width="5" height="5"/><rect x="4" y="15" width="5" height="5"/>'
                 '<rect x="15" y="15" width="5" height="5"/>'),
    "blur": ('<circle cx="12" cy="12" r="8" stroke-dasharray="2 2.6"/>'
             '<circle cx="12" cy="12" r="3.5"/>'),
    "spotlight": '<rect x="3" y="3" width="18" height="18" rx="2" stroke-dasharray="2 2"/><circle cx="12" cy="12" r="4.5"/>',
    "crop": '<path d="M7 3v14h14"/><path d="M3 7h14v14"/>',
    "copy": '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 00-1-1H5a1 1 0 00-1 1v10a1 1 0 001 1h3"/>',
    "save": '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>',
    "pin": '<path d="M9 4h6l-1 6 3 3H7l3-3z"/><path d="M12 13v7"/>',
    "ocr": '<path d="M4 8V5a1 1 0 011-1h3M16 4h3a1 1 0 011 1v3M20 16v3a1 1 0 01-1 1h-3M8 20H5a1 1 0 01-1-1v-3"/><path d="M8 9h8M8 12h8M8 15h5"/>',
    "redact": '<path d="M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6z"/><path d="M9 12l2 2 4-4"/>',
    "drag": '<path d="M12 3v18M3 12h18M12 3l-3 3M12 3l3 3M12 21l-3-3M12 21l3-3M3 12l3-3M3 12l3 3M21 12l-3-3M21 12l-3 3"/>',
    "undo": '<path d="M9 14L4 9l5-5"/><path d="M4 9h10a6 6 0 010 12h-3"/>',
    "redo": '<path d="M15 14l5-5-5-5"/><path d="M20 9H10a6 6 0 000 12h3"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "folder": '<path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z"/>',
    "trash": '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
    "fit": '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
    "close": '<path d="M6 6l12 12M18 6L6 18"/>',
    "callout": '<path d="M5 4h14a2 2 0 012 2v8a2 2 0 01-2 2h-7l-5 4v-4H5a2 2 0 01-2-2V6a2 2 0 012-2z"/><path d="M8 9h8M8 12h5"/>',
    "stamp": '<circle cx="12" cy="12" r="8.5"/><path d="M8.5 14a4 4 0 007 0"/><path d="M9.2 9.5h.01M14.8 9.5h.01" stroke-width="2.6"/>',
    "share": '<path d="M4 13v6a1 1 0 001 1h14a1 1 0 001-1v-6M12 3v12M8 7l4-4 4 4"/>',
    "mail": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/>',
    "chat": '<path d="M4 5h16v11H9l-5 4z"/>',
    "cloud": '<path d="M7 18a4.5 4.5 0 01-.5-9A6 6 0 0118 8.5 4.75 4.75 0 0117.5 18z"/>',
    "link": '<path d="M10 14a4 4 0 005.66 0l3-3a4 4 0 00-5.66-5.66l-1 1"/><path d="M14 10a4 4 0 00-5.66 0l-3 3a4 4 0 005.66 5.66l1-1"/>',
    "shape_rounded": '<rect x="3" y="6" width="18" height="12" rx="4"/>',
    "shape_rect": '<rect x="3" y="6" width="18" height="12"/>',
    "shape_ellipse": '<ellipse cx="12" cy="12" rx="9" ry="6.5"/>',
    "shape_pill": '<rect x="3" y="7" width="18" height="10" rx="5"/>',
    "tail": '<path d="M4 4h16v10H11l-6 6 2-6H4z"/>',
}


def _svg(name: str, color: str) -> bytes:
    body = _PATHS[name]
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
            f'stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
            f'{body}</svg>').encode()


@cache
def icon(name: str, color: str = "#d4d4d4", active: str = "#ffffff") -> QIcon:
    result = QIcon()
    for mode_color, mode in ((color, QIcon.Normal), (active, QIcon.Active)):
        for state in (QIcon.Off, QIcon.On):
            result.addPixmap(_render(name, active if state == QIcon.On else mode_color), mode, state)
    return result


def _render(name: str, color: str, size: int = 48) -> QPixmap:
    renderer = QSvgRenderer(QByteArray(_svg(name, color)))
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    renderer.render(p, QRectF(0, 0, size, size))
    p.end()
    return pix
