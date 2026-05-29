"""后端 i18n 支持。"""
from __future__ import annotations

import json
from pathlib import Path

_I18N_DIR = Path(__file__).parent / "web" / "i18n"


def load_translations(lang: str) -> dict[str, str]:
    path = _I18N_DIR / f"{lang}.json"
    if not path.exists():
        path = _I18N_DIR / "zh.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def t(key: str, lang: str = "zh", **vars: object) -> str:
    translations = load_translations(lang)
    text = translations.get(key, key)
    for k, v in vars.items():
        text = text.replace(f"{{{k}}}", str(v))
    return text
