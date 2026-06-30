"""Configuration file discovery, parsing, and profile resolution.

Searches for ``.autox.yaml`` starting from the current working directory
and walking up to filesystem root.  Values containing ``${ENV_VAR}``
are interpolated from the process environment at load time.

Resolution order (highest priority wins):

1. ``--target / -t``  — named service config, looked up directly
2. ``--profile / -p`` — profile name, service config resolved via mapping
3. ``AUTOX_PROFILE``  — environment variable, same as ``--profile``
4. ``defaults.profile`` in the config file
5. No config file found — returns ``None`` (caller falls back to ``.env``)
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from dotenv import find_dotenv, load_dotenv

from autox_tools.config._models import (
    _SERVICE_YAML_SECTIONS,
    AutoxConfig,
    MilvusConfig,
    OgxConfig,
    PgvectorConfig,
    Profile,
    RhoaiConfig,
    S3Config,
)

if TYPE_CHECKING:
    import argparse

_CONFIG_FILENAME = ".autox.yaml"
_ENV_VAR_RE = re.compile(r"\$\{([^}]+)}")

# ── File discovery ──────────────────────────────────────────────────────────


def find_config(start: str | Path | None = None) -> Path | None:
    """Walk up from *start* (default: cwd) looking for ``.autox.yaml``."""
    current = Path(start or os.getcwd()).resolve()
    for directory in (current, *current.parents):
        candidate = directory / _CONFIG_FILENAME
        if candidate.is_file():
            return candidate
    return None


# ── YAML parsing & env-var interpolation ────────────────────────────────────


def _interpolate(value: Any) -> Any:
    """Recursively replace ``${VAR}`` references with environment values."""
    if isinstance(value, str):
        return _ENV_VAR_RE.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _interpolate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate(v) for v in value]
    return value


def _parse_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() not in ("false", "0", "no")
    return default


def _build_s3(raw: dict) -> S3Config:
    return S3Config(
        endpoint=raw["endpoint"],
        access_key_id=raw["access_key_id"],
        secret_access_key=raw["secret_access_key"],
        region=raw.get("region", "us-east-1"),
        verify_tls=_parse_bool(raw.get("verify_tls"), True),
        bucket=raw.get("bucket", ""),
    )


def _build_rhoai(raw: dict) -> RhoaiConfig:
    return RhoaiConfig(
        kfp_url=raw["kfp_url"],
        token=raw["token"],
        project_name=raw["project_name"],
        verify_ssl=_parse_bool(raw.get("verify_ssl"), True),
        k8s_api_url=raw.get("k8s_api_url", ""),
        k8s_api_port=str(raw.get("k8s_api_port", "")),
    )


def _build_milvus(raw: dict) -> MilvusConfig:
    return MilvusConfig(
        host=raw["host"],
        port=int(raw["port"]),
        user=raw.get("user", ""),
        password=raw.get("password", ""),
        secure=_parse_bool(raw.get("secure"), False),
    )


def _build_pgvector(raw: dict) -> PgvectorConfig:
    return PgvectorConfig(
        host=raw["host"],
        port=int(raw["port"]),
        database=raw["database"],
        user=raw.get("user", ""),
        password=raw.get("password", ""),
        sslmode=raw.get("sslmode", "prefer"),
    )


def _build_ogx(raw: dict) -> OgxConfig:
    return OgxConfig(
        base_url=raw["base_url"],
        api_key=raw.get("api_key", ""),
    )


_BUILDERS: dict[str, Any] = {
    "s3": _build_s3,
    "rhoai": _build_rhoai,
    "milvus": _build_milvus,
    "pgvector": _build_pgvector,
    "ogx": _build_ogx,
}

_YAML_PATHS: dict[str, tuple[str, ...]] = {
    "s3": ("s3",),
    "rhoai": ("rhoai",),
    "milvus": ("vs", "milvus"),
    "pgvector": ("vs", "pgvector"),
    "ogx": ("ogx",),
}


def _build_profile(raw: dict) -> Profile:
    return Profile(
        s3=raw.get("s3", ""),
        artifacts_s3=raw.get("artifacts_s3", ""),
        rhoai=raw.get("rhoai", ""),
        milvus=raw.get("milvus", ""),
        pgvector=raw.get("pgvector", ""),
        ogx=raw.get("ogx", ""),
    )


def load_config(path: Path | None = None) -> AutoxConfig | None:
    """Parse ``.autox.yaml`` and return a typed ``AutoxConfig``.

    Loads ``.env`` into ``os.environ`` before interpolation so that
    ``${ENV_VAR}`` references resolve during migration from ``.env``
    to profile-based configuration.

    Returns ``None`` when no config file is found and *path* is not given.
    """
    if path is None:
        path = find_config()
    if path is None:
        return None

    load_dotenv(find_dotenv(usecwd=True))

    with open(path) as fh:
        raw = yaml.safe_load(fh)

    if not isinstance(raw, dict):
        sys.exit(f"Invalid config: {path} must be a YAML mapping")

    raw = _interpolate(raw)

    defaults = raw.get("defaults") or {}
    default_profile = defaults.get("profile", "")

    profiles: dict[str, Profile] = {}
    for name, body in (raw.get("profiles") or {}).items():
        profiles[name] = _build_profile(body or {})

    cfg = AutoxConfig(default_profile=default_profile, profiles=profiles)

    for section, builder in _BUILDERS.items():
        yaml_path = _YAML_PATHS.get(section, (section,))
        section_data: dict = raw
        for key in yaml_path:
            section_data = (section_data or {}).get(key) or {}
        yaml_label = ".".join(yaml_path)
        configs: dict = {}
        for name, body in section_data.items():
            try:
                configs[name] = builder(body or {})
            except (KeyError, TypeError, ValueError) as exc:
                sys.exit(f"Invalid {yaml_label}.{name} in {path}: {exc}")
        setattr(cfg, section, configs)

    return cfg


# ── Profile / target resolution ────────────────────────────────────────────


def resolve(service_type: str, args: argparse.Namespace) -> Any:
    """Resolve a typed service config from CLI args and config file.

    Returns ``None`` when no config file exists or the service is not
    configured — the caller should fall back to ``.env`` variables.
    """
    target: str = getattr(args, "target", None) or ""
    profile_name: str = getattr(args, "profile", None) or ""

    cfg = load_config()
    if cfg is None:
        return None

    yaml_section = _SERVICE_YAML_SECTIONS.get(service_type, service_type)
    available: dict = cfg.service_configs(service_type)

    if target:
        if target not in available:
            names = ", ".join(sorted(available)) or "(none defined)"
            sys.exit(f"Unknown {yaml_section} target '{target}'. Available: {names}")
        return available[target]

    active_profile = profile_name or os.getenv("AUTOX_PROFILE", "") or cfg.default_profile
    if not active_profile:
        return None

    if active_profile not in cfg.profiles:
        names = ", ".join(sorted(cfg.profiles)) or "(none defined)"
        sys.exit(f"Unknown profile '{active_profile}'. Available: {names}")

    profile = cfg.profiles[active_profile]
    config_name = getattr(profile, service_type, "")
    if not config_name:
        return None

    if config_name not in available:
        sys.exit(
            f"Profile '{active_profile}' references {yaml_section} config "
            f"'{config_name}' which is not defined in the config file."
        )
    return available[config_name]


# ── CLI helpers ─────────────────────────────────────────────────────────────


def add_profile_args(parser: argparse.ArgumentParser, *, target: bool = False) -> None:
    """Add ``--profile / -p`` (and optionally ``--target / -t``) to a parser."""
    parser.add_argument(
        "--profile", "-p",
        default=None,
        help="Configuration profile name (from .autox.yaml)",
    )
    if target:
        parser.add_argument(
            "--target", "-t",
            default=None,
            help="Named service configuration to use directly (from .autox.yaml)",
        )
