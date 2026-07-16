"""Load YAML config files."""

import os

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "PyYAML is required. Install it with:  pip install -r requirements.txt"
    ) from exc

CONFIG_DIR = "config"


def _load_yaml(name):
    path = os.path.join(CONFIG_DIR, name)
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_companies():
    data = _load_yaml("companies.yaml")
    return data.get("companies", [])


def load_settings():
    return _load_yaml("settings.yaml")
