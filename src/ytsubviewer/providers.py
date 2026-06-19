"""Model provider registry for OpenAI-compatible translation APIs."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ModelProvider:
    name: str
    label: str
    base_url: str
    models: list[str] = field(default_factory=list)
    api_key_env: str = ""
    api_key: str = ""

    def resolve_api_key(self) -> str:
        if self.api_key:
            return self.api_key
        if self.api_key_env:
            key = os.getenv(self.api_key_env, "")
            if key:
                return key
        # 从配置文件读取 API Key
        try:
            from ytsubviewer.config import Settings, decrypt_value
            settings = Settings.load()
            config_path = settings.config_path
            if config_path.exists():
                with open(config_path, 'r') as f:
                    config = json.load(f)
                encrypted = config.get(f"api_key_{self.name}", "")
                if encrypted:
                    return decrypt_value(encrypted)
        except Exception:
            pass
        return ""

    def has_key(self) -> bool:
        if self.name == "ollama":
            return True
        return bool(self.resolve_api_key())

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "label": self.label,
            "base_url": self.base_url,
            "models": self.models,
            "api_key_env": self.api_key_env,
            "has_key": self.has_key(),
        }


BUILTIN_PROVIDERS: list[ModelProvider] = [
    ModelProvider(
        name="deepseek",
        label="DeepSeek",
        base_url="https://api.deepseek.com",
        models=["deepseek-chat", "deepseek-reasoner"],
        api_key_env="DEEPSEEK_API_KEY",
    ),
    ModelProvider(
        name="deepseek-test",
        label="DeepSeek Test (本地测试)",
        base_url="https://api.deepseek.com/v1",
        models=["deepseek-chat", "deepseek-reasoner"],
    ),
    ModelProvider(
        name="openai",
        label="OpenAI",
        base_url="https://api.openai.com/v1",
        models=["gpt-4o", "gpt-4o-mini", "gpt-4.1-mini", "gpt-4.1-nano"],
        api_key_env="OPENAI_API_KEY",
    ),
    ModelProvider(
        name="qwen",
        label="通义千问",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        models=["qwen-plus", "qwen-turbo", "qwen-max"],
        api_key_env="DASHSCOPE_API_KEY",
    ),
    ModelProvider(
        name="moonshot",
        label="Moonshot",
        base_url="https://api.moonshot.cn/v1",
        models=["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
        api_key_env="MOONSHOT_API_KEY",
    ),
    ModelProvider(
        name="ollama",
        label="Ollama (本地)",
        base_url="http://localhost:11434/v1",
        models=["qwen2.5:7b", "llama3:8b", "deepseek-r1:7b"],
    ),
]


def get_builtin_providers() -> list[ModelProvider]:
    return list(BUILTIN_PROVIDERS)


def get_provider(name: str) -> ModelProvider | None:
    for p in BUILTIN_PROVIDERS:
        if p.name == name:
            return p
    return None


def get_default_provider() -> ModelProvider:
    return BUILTIN_PROVIDERS[0]
