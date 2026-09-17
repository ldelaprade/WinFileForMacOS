from __future__ import annotations

import socket
import threading

try:
    from pyftpdlib.authorizers import DummyAuthorizer
    from pyftpdlib.handlers import FTPHandler
    from pyftpdlib.servers import FTPServer as _FTPServer

    _PYFTPDLIB_AVAILABLE = True
except ImportError:
    _PYFTPDLIB_AVAILABLE = False


def ftp_server_available() -> bool:
    return _PYFTPDLIB_AVAILABLE


def local_ip_address() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


class FtpShare:
    """A lightweight FTP server sharing a single folder from a background thread."""

    def __init__(
        self,
        path: str,
        share_name: str,
        port: int,
        username: str,
        password: str,
        read_only: bool,
    ) -> None:
        self.path = path
        self.share_name = share_name
        self.port = port
        self.username = username
        self.password = password
        self.read_only = read_only
        self._server: _FTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> str | None:
        if not _PYFTPDLIB_AVAILABLE:
            return "pyftpdlib is not installed. Run: pip install pyftpdlib"

        authorizer = DummyAuthorizer()
        perm = "elr" if self.read_only else "elradfmwMT"
        try:
            if self.username:
                authorizer.add_user(self.username, self.password, self.path, perm=perm)
            else:
                authorizer.add_anonymous(self.path, perm=perm)

            # Subclass per-instance so banners/authorizers of concurrent shares don't clash.
            handler = type(f"WinFileXPFtpHandler{self.port}", (FTPHandler,), {})
            handler.authorizer = authorizer
            handler.banner = f"WinFileXP FTP Share: {self.share_name}"

            self._server = _FTPServer(("0.0.0.0", self.port), handler)
        except OSError as error:
            return str(error)

        # handle_exit must be False: signal handlers can only be installed on the main thread.
        self._thread = threading.Thread(
            target=self._serve_forever_quietly,
            daemon=True,
        )
        self._thread.start()
        return None

    def _serve_forever_quietly(self) -> None:
        try:
            self._server.serve_forever(handle_exit=False)
        except OSError:
            # Expected when stop() closes the ioloop's sockets while polling.
            pass

    def stop(self, timeout: float = 2.0) -> None:
        if self._server is not None:
            self._server.close_all()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout)
        self._server = None
        self._thread = None

    @property
    def url(self) -> str:
        auth = f"{self.username}@" if self.username else ""
        return f"ftp://{auth}{local_ip_address()}:{self.port}/"
