"""Keep one SnipForge running; later launches hand their command to it and exit."""
from __future__ import annotations

import getpass

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from shiboken6 import isValid

SERVER_NAME = f"SnipForge-{getpass.getuser()}"
COMMANDS = {"home", "snip", "background"}
TIMEOUT_MS = 500


def send_to_running(command: str) -> bool:
    """Returns True if another instance received the command."""
    socket = QLocalSocket()
    socket.connectToServer(SERVER_NAME)
    if not socket.waitForConnected(TIMEOUT_MS):
        return False
    socket.write(command.encode())
    socket.waitForBytesWritten(TIMEOUT_MS)
    socket.disconnectFromServer()
    return True


class InstanceServer(QObject):
    command = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._server = QLocalServer(self)
        QLocalServer.removeServer(SERVER_NAME)  # clear a stale socket left by a crash
        self.listening = self._server.listen(SERVER_NAME)
        self._server.newConnection.connect(self._accept)

    def _accept(self) -> None:
        while (socket := self._server.nextPendingConnection()) is not None:
            socket.readyRead.connect(lambda s=socket: self._read(s))
            socket.disconnected.connect(lambda s=socket: self._finish(s))
            # The launcher writes and disconnects immediately, so its bytes are often already
            # buffered and readyRead won't fire again: read now or the command is lost.
            if socket.bytesAvailable():
                self._read(socket)

    def _finish(self, socket: QLocalSocket) -> None:
        self._read(socket)
        if isValid(socket):
            socket.deleteLater()

    def _read(self, socket: QLocalSocket) -> None:
        if not isValid(socket):  # already torn down (e.g. while the app is quitting)
            return
        text = bytes(socket.readAll()).decode(errors="ignore").strip()
        if text in COMMANDS:
            self.command.emit(text)
