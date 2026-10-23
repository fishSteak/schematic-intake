"""Explicit model profiles; never inherit a coding-agent session or credentials."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlsplit

from .errors import ConfigurationError


@dataclass(frozen=True)
class Profile:
    base_url: str
    model: str
    provider: str = "openai-compatible"
    api_key: str = field(default="", repr=False, compare=False)
    api_key_env: str = ""
    timeout: float = 120.0
    max_tokens: int = 8192
    max_turns: int = 16
    max_requests: int = 24
    max_retries: int = 2
    retry_backoff: float = 0.5
    stream: bool = False
    structured_output: str = "json_schema"
    strict_schema: bool = False
    supports_tools: bool = True
    supports_images: bool = True
    token_parameter: str = "max_tokens"

    def __post_init__(self) -> None:
        if not isinstance(self.provider, str) or self.provider not in {"openai-compatible", "openai_compatible", "openai", "ollama"}:
            raise ConfigurationError("Unsupported LLM provider protocol")
        try:
            parsed = urlsplit(self.base_url)
            valid_url = parsed.scheme in {"http", "https"} and bool(parsed.hostname) and parsed.port != 0
        except (TypeError, ValueError):
            valid_url = False
            parsed = None
        if not valid_url or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ConfigurationError("LLM base_url must be an HTTP(S) URL without credentials, query or fragment")
        if not isinstance(self.model, str) or not self.model.strip() or any(ord(c) < 32 for c in self.model):
            raise ConfigurationError("LLM model must be explicitly configured")
        if not isinstance(self.api_key, str) or any(c in self.api_key for c in "\r\n"):
            raise ConfigurationError("Invalid model credential")
        if not isinstance(self.api_key_env, str) or (self.api_key_env and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.api_key_env)):
            raise ConfigurationError("api_key_env must name an environment variable")
        for name in ("max_tokens", "max_turns", "max_requests"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ConfigurationError(f"{name} must be a positive integer")
        if isinstance(self.max_retries, bool) or not isinstance(self.max_retries, int) or not 0 <= self.max_retries <= 5:
            raise ConfigurationError("max_retries must be an integer between zero and five")
        for name in ("timeout", "retry_backoff"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ConfigurationError(f"{name} must be a finite nonnegative number")
        if self.timeout <= 0:
            raise ConfigurationError("timeout must be greater than zero")
        for name in ("stream", "strict_schema", "supports_tools", "supports_images"):
            if not isinstance(getattr(self, name), bool):
                raise ConfigurationError(f"{name} must be a boolean")
        if self.structured_output not in {"json_schema", "json_object", "none"}:
            raise ConfigurationError("structured_output must be json_schema, json_object or none")
        if self.token_parameter not in {"max_tokens", "max_completion_tokens"}:
            raise ConfigurationError("Unsupported output token parameter")

    @property
    def endpoint(self) -> str:
        base = self.base_url.rstrip("/")
        return base if base.endswith("/chat/completions") else base + "/chat/completions"

    def public(self) -> dict[str, Any]:
        """Non-secret, UI-safe profile metadata."""
        data = {f.name: getattr(self, f.name) for f in fields(self) if f.name != "api_key"}
        data["api_key_configured"] = bool(self.api_key)
        return data

    def signature(self) -> str:
        """Execution policy signature excludes credential values and references."""
        data = self.public()
        for name in ("api_key_env", "api_key_configured"):
            data.pop(name, None)
        data["protocol_version"] = "llm-chat-v1"
        return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def load_profile(path: str | Path | None = None, task: str = "review") -> Profile:
    """Read a flat profile or a named profiles/tasks document, with explicit env overrides.

    A named document has ``profiles: {id: profile}``, ``tasks: {review: id}``
    and optionally ``default_profile: id``. No files outside the explicitly
    selected path are consulted.
    """
    selected = path or os.environ.get("SCHEMATIC_LLM_PROFILE")
    data: dict[str, Any] = {}
    if selected:
        try:
            raw = json.loads(Path(selected).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, TypeError):
            raise ConfigurationError("Cannot read the configured LLM profile") from None
        if not isinstance(raw, dict):
            raise ConfigurationError("LLM profile must be a JSON object")
        if "profiles" in raw:
            profiles = raw.get("profiles")
            mapping = raw.get("tasks") or {}
            if not isinstance(profiles, dict) or not isinstance(mapping, dict):
                raise ConfigurationError("Invalid named LLM profile document")
            name = mapping.get(task) or raw.get("default_profile")
            if not isinstance(name, str) or not name or not isinstance(profiles.get(name), dict):
                raise ConfigurationError("No LLM profile is selected for this task")
            data = dict(profiles[name])
        else:
            data = dict(raw)
    known = {f.name for f in fields(Profile)}
    # Frontend/store identifiers are metadata, not model protocol parameters.
    data = {key: value for key, value in data.items() if key in known}
    for name in ("base_url", "model", "provider"):
        value = os.environ.get("SCHEMATIC_LLM_" + name.upper())
        if value and not data.get(name):
            data[name] = value
    key_ref = data.get("api_key_env", "")
    if key_ref:
        if not isinstance(key_ref, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key_ref):
            raise ConfigurationError("api_key_env must name an environment variable")
        value = os.environ.get(key_ref)
        if not value and not data.get("api_key"):
            raise ConfigurationError("The configured LLM credential environment variable is not set")
        if value:
            data["api_key"] = value
    if os.environ.get("SCHEMATIC_LLM_API_KEY") and not data.get("api_key"):
        data["api_key"] = os.environ["SCHEMATIC_LLM_API_KEY"]
    if not data.get("base_url") or not data.get("model"):
        raise ConfigurationError("Configure an LLM base_url and model before starting model tasks")
    try:
        return Profile(**data)
    except TypeError:
        raise ConfigurationError("Invalid LLM profile fields") from None


def configuration_available() -> bool:
    try:
        load_profile()
        return True
    except ConfigurationError:
        return False


def profile_fingerprint(profile: Profile | None = None, *, task: str = "review") -> str:
    """Stable model policy signature; absence is distinguishable from any profile."""
    if profile is None:
        try:
            profile = load_profile(task=task)
        except ConfigurationError:
            return hashlib.sha256(b"schematic-llm:unconfigured:v1").hexdigest()
    return profile.signature()
