"""Cliente del backend e inicio de sesión con Google vía el navegador."""

import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import keyring
import requests

from .config import BACKEND_URL, KEYRING_SERVICE

_TOKEN_KEY = "session"


class AuthError(Exception):
    pass


def load_token() -> str | None:
    return keyring.get_password(KEYRING_SERVICE, _TOKEN_KEY)


def save_token(token: str) -> None:
    keyring.set_password(KEYRING_SERVICE, _TOKEN_KEY, token)


def clear_token() -> None:
    try:
        keyring.delete_password(KEYRING_SERVICE, _TOKEN_KEY)
    except keyring.errors.PasswordDeleteError:
        pass


def browser_login(timeout_s: int = 300) -> str:
    """Abre el login de Google en el navegador y espera el token en un puerto local."""
    result: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlparse(self.path)
            token = parse_qs(url.query).get("token", [None])[0]
            if url.path == "/callback" and token:
                result["token"] = token
                body = "<h2>Sesión iniciada</h2><p>Ya puedes cerrar esta pestaña y volver a la app.</p>"
                self.send_response(200)
            else:
                body = "<p>Solicitud no válida.</p>"
                self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.timeout = 1
    port = server.server_address[1]
    webbrowser.open(f"{BACKEND_URL}/auth/login?client=desktop&port={port}")

    stop = threading.Timer(timeout_s, lambda: result.setdefault("timeout", "1"))
    stop.start()
    try:
        while "token" not in result and "timeout" not in result:
            server.handle_request()
    finally:
        stop.cancel()
        server.server_close()
    if "token" not in result:
        raise AuthError("No se completó el inicio de sesión a tiempo")
    save_token(result["token"])
    return result["token"]


class Api:
    def __init__(self, token: str):
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"Bearer {token}"

    def _req(self, method: str, path: str, timeout: int = 60, **kw):
        r = self.s.request(method, f"{BACKEND_URL}{path}", timeout=timeout, **kw)
        if r.status_code == 401:
            raise AuthError("La sesión expiró")
        if not r.ok:
            try:
                detail = r.json().get("detail", r.text)
            except ValueError:
                detail = r.text
            raise RuntimeError(f"{r.status_code}: {detail}")
        return r.json() if r.content else None

    def me(self) -> dict:
        return self._req("GET", "/api/me")

    def current_events(self) -> list[dict]:
        return self._req("GET", "/api/calendar/current")

    def create_meeting(self, payload: dict) -> dict:
        # En Vercel el servidor responde hasta tener la minuta lista (puede tardar unos minutos)
        return self._req("POST", "/api/meetings", json=payload, timeout=330)
