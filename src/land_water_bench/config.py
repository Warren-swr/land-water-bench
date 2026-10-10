"""Explicit direct-API configuration; credentials are read only from the environment."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import yaml


@dataclass
class ModelConfig:
    name: str
    model: str
    wire: str
    url: str = field(repr=False)
    key: str = field(repr=False)
    reasoning: dict
    max_tokens: int = 65536

    def public(self) -> dict:
        return {"name": self.name, "requested_model": self.model, "wire": self.wire,
                "reasoning": self.reasoning, "max_output_tokens": self.max_tokens,
                "tools": [], "system_prompt": None, "temperature": "provider default"}

    def request(self, prompt: str) -> tuple[dict, dict]:
        if self.wire == "responses":
            body = {"model": self.model, "input": [{"role": "user", "content": prompt}],
                    "max_output_tokens": self.max_tokens, "store": False}
            if self.reasoning:
                body["reasoning"] = self.reasoning
            return {"Authorization": "Bearer " + self.key}, body
        body = {"model": self.model, "messages": [{"role": "user", "content": prompt}],
                "max_tokens": self.max_tokens}
        if self.reasoning:
            body["thinking"] = self.reasoning
        return {"x-api-key": self.key, "anthropic-version": "2023-06-01"}, body


def safe_name(name: str) -> bool:
    return isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", name) is not None


def required_env(name: str) -> str:
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError("Expected an environment-variable name")
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"Set the {name} environment variable")
    return value


def load_models(path: Path, names: list[str]) -> list[ModelConfig]:
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict) or set(raw) != {"models"} or not isinstance(raw["models"], dict):
        raise ValueError("Expected a standalone config with a models mapping; see configs/models.example.yaml")
    models = []
    allowed = {"model", "model_env", "wire", "url", "url_env", "api_key_env", "reasoning", "max_output_tokens"}
    for name in names:
        if not safe_name(name) or name in {"reference", "data", "attempts", "lifecycle", "summary", "config", "manifest", "verification"}:
            raise ValueError("Invalid or reserved model name")
        item = raw["models"].get(name)
        if not isinstance(item, dict) or set(item) - allowed:
            raise ValueError(f"Unknown model or configuration fields: {name}")
        wire = item.get("wire")
        if wire not in {"responses", "messages"}:
            raise ValueError(f"Unsupported wire protocol for {name}")
        if ("url" in item) == ("url_env" in item) or ("model" in item) == ("model_env" in item):
            raise ValueError(f"Set exactly one url/url_env and one model/model_env for {name}")
        url = required_env(item["url_env"]) if "url_env" in item else item["url"]
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError(f"Use a full HTTP(S) endpoint without credentials or query parameters for {name}")
        model = required_env(item["model_env"]) if "model_env" in item else item["model"]
        reasoning = item.get("reasoning", {})
        cap = item.get("max_output_tokens", 65536)
        if not isinstance(model, str) or not model or not isinstance(reasoning, dict) or type(cap) is not int or cap < 1:
            raise ValueError(f"Invalid model, reasoning, or output cap for {name}")
        models.append(ModelConfig(name, model, wire, url, required_env(item.get("api_key_env")), reasoning, cap))
    if not models or len({m.name for m in models}) != len(models):
        raise ValueError("Choose at least one distinct model")
    return models


def redact(value, secrets: list[str]):
    if isinstance(value, dict):
        return {k: "[REDACTED]" if k.lower() in {
            "authorization", "x-api-key", "api_key", "token", "access_token"
        } else redact(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, secrets) for v in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
    return value
