"""Background device-code login flow for the web UI."""

from __future__ import annotations

import threading
from typing import Any

from color_book_generator.auth import codex


class AuthFlowState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.pending = False
        self.user_code: str | None = None
        self.verification_url = codex.DEVICE_LOGIN_URL
        self.error: str | None = None

    def start(self) -> dict[str, Any]:
        with self._lock:
            if codex.is_authenticated():
                return self.as_dict()
            if self.pending:
                return self.as_dict()
            self.pending = True
            self.user_code = None
            self.error = None
        thread = threading.Thread(target=self._run, daemon=True)
        thread.start()
        return self.as_dict()

    def _run(self) -> None:
        try:
            codex.authenticate(
                on_code=self._on_code,
                on_wait=lambda: None,
            )
            with self._lock:
                self.pending = False
                self.error = None
        except Exception as exc:  # surfaced to the browser
            with self._lock:
                self.pending = False
                self.error = str(exc)

    def _on_code(self, user_code: str, url: str) -> None:
        with self._lock:
            self.user_code = user_code
            self.verification_url = url

    def logout(self) -> dict[str, Any]:
        codex.clear_tokens()
        with self._lock:
            self.pending = False
            self.user_code = None
            self.error = None
        return self.as_dict()

    def as_dict(self) -> dict[str, Any]:
        return {
            "authenticated": codex.is_authenticated(),
            "pending": self.pending,
            "user_code": self.user_code,
            "verification_url": self.verification_url,
            "error": self.error,
        }


flow = AuthFlowState()
