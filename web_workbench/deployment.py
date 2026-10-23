"""Explicit subpath and same-origin reverse proxy configuration."""
from __future__ import annotations

from dataclasses import dataclass
from http.cookiejar import CookieJar, DefaultCookiePolicy
import ipaddress
import os
from pathlib import Path
import re
from urllib.parse import urlencode, urlsplit

import httpx


class NoSharedCookies(DefaultCookiePolicy):
    """Connection pooling must not become a shared browser authentication jar."""
    def set_ok(self, cookie, request):
        return False

    def return_ok(self, cookie, request):
        return False


def auth_client(transport=None):
    return httpx.AsyncClient(timeout=5, follow_redirects=False, trust_env=False,
        transport=transport, cookies=CookieJar(policy=NoSharedCookies()))


def path_prefix(value):
    value = str(value or "").rstrip("/")
    if value and (not re.fullmatch(r"(?:/[A-Za-z0-9._~-]+)+", value) or any(part in {".", ".."} for part in value.split("/"))):
        raise ValueError("root_path 必须是同源绝对路径，如 /schematic")
    return value


def authority(host):
    """Parse an exact host authority, including bracketed IPv6 and a valid port."""
    try:
        value = urlsplit("//" + host)
        if not value.hostname or value.username or value.password or value.path or value.query or value.fragment:
            raise ValueError
        if value.port is not None and not 0 < value.port <= 65535:
            raise ValueError
        return value.hostname.lower().rstrip("."), value.port
    except (ValueError, TypeError):
        raise ValueError("无效 Host") from None


def origin_tuple(value):
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or parsed.path or parsed.query or parsed.fragment:
            raise ValueError
        hostname, port = authority(parsed.netloc)
        return parsed.scheme, hostname, port or (443 if parsed.scheme == "https" else 80)
    except (ValueError, TypeError):
        raise ValueError("无效 Origin") from None


@dataclass(frozen=True)
class Deployment:
    root_path: str
    allowed_hosts: frozenset[str]
    auth_check_url: str | None
    login_url: str
    ui_dir: Path

    @classmethod
    def configure(cls, repository, *, root_path=None, allowed_hosts=None, auth_check_url=None, login_url=None, ui_dir=None):
        prefix = path_prefix(os.environ.get("SCHEMATIC_ROOT_PATH", "") if root_path is None else root_path)
        raw_hosts = os.environ.get("SCHEMATIC_ALLOWED_HOSTS", "localhost,127.0.0.1,::1,testserver") if allowed_hosts is None else allowed_hosts
        entries = raw_hosts.split(",") if isinstance(raw_hosts, str) else raw_hosts
        hosts = set()
        for entry in entries:
            entry = str(entry).strip().lower()
            if not entry or entry == "*":
                raise ValueError("allowed_hosts 需为明确主机名列表，不能使用通配符")
            try:
                ipaddress.IPv6Address(entry)
                entry = "[" + entry + "]"
            except ValueError:
                pass
            hostname, port = authority(entry)
            if port is not None:
                raise ValueError("allowed_hosts 只填主机名，不含端口")
            hosts.add(hostname)
        if not hosts:
            raise ValueError("allowed_hosts 不能为空")
        check = os.environ.get("SCHEMATIC_AUTH_CHECK_URL") if auth_check_url is None else auth_check_url
        if check:
            try:
                parsed = urlsplit(check)
                hostname, _ = authority(parsed.netloc)
                local = hostname == "localhost" or ipaddress.ip_address(hostname).is_loopback
                if parsed.scheme not in {"http", "https"} or not local or parsed.fragment:
                    raise ValueError
            except (ValueError, TypeError):
                raise ValueError("auth_check_url 必须是固定的本机回环 HTTP(S) 地址") from None
        login = os.environ.get("SCHEMATIC_LOGIN_URL", "/login") if login_url is None else login_url
        normalized_login = path_prefix(login)
        if not normalized_login or login.rstrip("/") != normalized_login:
            raise ValueError("login_url 必须是同源相对地址，如 /login")
        assets = ui_dir or os.environ.get("SCHEMATIC_UI_DIR") or Path(repository) / "report_ui/dist"
        return cls(prefix, frozenset(hosts), check or None, normalized_login, Path(assets).resolve())

    def public_path(self, path):
        if not path or not self.root_path or path == self.root_path or path.startswith(self.root_path + "/"):
            return path
        return self.root_path + "/" + str(path).lstrip("/")

    def request_path(self, request):
        path = request.scope.get("path", "/")
        return self.public_path(path) + ("?" + request.url.query if request.url.query else "")

    def local_path(self, request):
        path = request.scope.get("path", "/")
        if self.root_path and (path == self.root_path or path.startswith(self.root_path + "/")):
            path = path[len(self.root_path):]
        return path or "/"

    def login_redirect(self, target):
        return self.login_url + "?" + urlencode({"next": target})
