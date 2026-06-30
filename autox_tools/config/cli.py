"""CLI entry point for configuration management.

Usage::

    uv run config list
    uv run config show <profile>
    uv run config validate
    uv run config init
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from autox_tools.config._loader import _CONFIG_FILENAME, find_config, load_config

_MASK_FIELDS = {"secret_access_key", "password", "token", "api_key"}


def _mask(value: str) -> str:
    if not value or len(value) <= 6:
        return "***"
    return value[:3] + "***" + value[-3:]


def _format_config(cfg: object) -> dict[str, str]:
    """Convert a frozen dataclass to a display dict with secrets masked."""
    from dataclasses import fields
    result: dict[str, str] = {}
    for f in fields(cfg):  # type: ignore[arg-type]
        val = getattr(cfg, f.name)
        if f.name in _MASK_FIELDS and val:
            val = _mask(str(val))
        result[f.name] = str(val)
    return result


def cmd_list(args: argparse.Namespace) -> None:
    """List available profiles and service configurations."""
    cfg = load_config()
    if cfg is None:
        sys.exit(f"No {_CONFIG_FILENAME} found. Run 'config init' to create one.")

    print("Profiles:")
    if not cfg.profiles:
        print("  (none)")
    for name, profile in sorted(cfg.profiles.items()):
        default_marker = " (default)" if name == cfg.default_profile else ""
        print(f"  {name}{default_marker}")
        from dataclasses import fields
        for f in fields(profile):
            ref = getattr(profile, f.name)
            if ref:
                print(f"    {f.name}: {ref}")

    sections = [
        ("s3", cfg.s3),
        ("rhoai", cfg.rhoai),
        ("vs.milvus", cfg.milvus),
        ("vs.pgvector", cfg.pgvector),
        ("ogx", cfg.ogx),
    ]
    for section_name, configs in sections:
        if configs:
            print(f"\n{section_name} configs:")
            for name in sorted(configs):
                print(f"  {name}")


def cmd_show(args: argparse.Namespace) -> None:
    """Show resolved config values for a profile."""
    cfg = load_config()
    if cfg is None:
        sys.exit(f"No {_CONFIG_FILENAME} found.")

    profile_name = args.profile_name
    if profile_name not in cfg.profiles:
        names = ", ".join(sorted(cfg.profiles)) or "(none)"
        sys.exit(f"Unknown profile '{profile_name}'. Available: {names}")

    profile = cfg.profiles[profile_name]
    print(f"Profile: {profile_name}")

    mappings = [
        ("s3", profile.s3, cfg.s3),
        ("artifacts_s3", profile.artifacts_s3, cfg.s3),
        ("rhoai", profile.rhoai, cfg.rhoai),
        ("vs.milvus", profile.milvus, cfg.milvus),
        ("vs.pgvector", profile.pgvector, cfg.pgvector),
        ("ogx", profile.ogx, cfg.ogx),
    ]
    for label, config_name, store in mappings:
        if not config_name:
            continue
        print(f"\n  {label} -> {config_name}")
        if config_name in store:
            for key, val in _format_config(store[config_name]).items():
                print(f"    {key}: {val}")
        else:
            print(f"    (NOT FOUND — config '{config_name}' is not defined)")


def cmd_validate(args: argparse.Namespace) -> None:
    """Check config file for errors."""
    path = find_config()
    if path is None:
        sys.exit(f"No {_CONFIG_FILENAME} found.")

    cfg = load_config(path)
    if cfg is None:
        sys.exit("Failed to load config.")

    errors: list[str] = []

    for profile_name, profile in cfg.profiles.items():
        mappings = [
            ("s3", profile.s3, cfg.s3),
            ("artifacts_s3", profile.artifacts_s3, cfg.s3),
            ("rhoai", profile.rhoai, cfg.rhoai),
            ("vs.milvus", profile.milvus, cfg.milvus),
            ("vs.pgvector", profile.pgvector, cfg.pgvector),
            ("ogx", profile.ogx, cfg.ogx),
        ]
        for label, config_name, store in mappings:
            if config_name and config_name not in store:
                errors.append(
                    f"Profile '{profile_name}': {label} references "
                    f"'{config_name}' which is not defined"
                )

    if cfg.default_profile and cfg.default_profile not in cfg.profiles:
        errors.append(f"Default profile '{cfg.default_profile}' is not defined")

    if errors:
        print(f"Validation failed ({path}):")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)

    print(f"Config valid: {path}")
    print(f"  {len(cfg.profiles)} profile(s), "
          f"{len(cfg.s3)} s3, {len(cfg.rhoai)} rhoai, "
          f"{len(cfg.milvus)} vs.milvus, {len(cfg.pgvector)} vs.pgvector, "
          f"{len(cfg.ogx)} ogx config(s)")


def cmd_init(args: argparse.Namespace) -> None:
    """Generate a starter .autox.yaml from the example template."""
    target = Path.cwd() / _CONFIG_FILENAME
    if target.exists() and not args.force:
        sys.exit(f"{_CONFIG_FILENAME} already exists. Use --force to overwrite.")

    example = Path(__file__).resolve().parent.parent.parent / ".autox.yaml.example"
    content = example.read_text() if example.is_file() else _MINIMAL_TEMPLATE

    target.write_text(content)
    print(f"Created {target}")
    print("Edit this file to add your service credentials and profiles.")


_MINIMAL_TEMPLATE = """\
# autox-tools configuration
# Docs: see .autox.yaml.example for a fully annotated reference.

defaults:
  profile: dev

profiles:
  dev:
    s3: my-s3
    # artifacts_s3: my-s3
    # rhoai: my-cluster
    # milvus: my-milvus
    # pgvector: my-pgvector
    # ogx: my-ogx

s3:
  my-s3:
    endpoint: https://minio.apps.cluster.example.com
    access_key_id: ${AWS_ACCESS_KEY_ID}
    secret_access_key: ${AWS_SECRET_ACCESS_KEY}
    region: us-east-1
    verify_tls: false

# vs:
#   milvus:
#     my-milvus:
#       host: milvus.apps.cluster.example.com
#       port: 19530
#   pgvector:
#     my-pgvector:
#       host: pgvector.apps.cluster.example.com
#       port: 5432
#       database: vectordb
"""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="config",
        description="Manage autox-tools configuration profiles.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List profiles and service configs")

    p = sub.add_parser("show", help="Show resolved config for a profile")
    p.add_argument("profile_name", help="Profile name to display")

    sub.add_parser("validate", help="Check config file for errors")

    p = sub.add_parser("init", help="Generate a starter .autox.yaml")
    p.add_argument("--force", action="store_true", help="Overwrite existing file")

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    commands = {
        "list": cmd_list,
        "show": cmd_show,
        "validate": cmd_validate,
        "init": cmd_init,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
