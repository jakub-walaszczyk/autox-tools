"""Unit tests for the configuration loader and profile resolver."""

from __future__ import annotations

import argparse
import os
import tempfile
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from autox_tools.config._loader import (
    _interpolate,
    find_config,
    load_config,
    resolve,
)
from autox_tools.config._models import (
    MilvusConfig,
    OgxConfig,
    PgvectorConfig,
    Profile,
    RhoaiConfig,
    S3Config,
)

# ── Fixtures ────────────────────────────────────────────────────────────────

_SAMPLE_YAML = textwrap.dedent("""\
    defaults:
      profile: dev

    profiles:
      dev:
        s3: minio-dev
        artifacts_s3: minio-dev
        rhoai: dev-cluster
        milvus: local-milvus
        pgvector: local-pg
        ogx: dev-ogx
      staging:
        s3: aws-staging
        rhoai: staging-cluster

    s3:
      minio-dev:
        endpoint: https://minio.dev.example.com
        access_key_id: dev-key
        secret_access_key: dev-secret
        region: us-east-1
        verify_tls: false
      aws-staging:
        endpoint: https://s3.amazonaws.com
        access_key_id: staging-key
        secret_access_key: staging-secret
        region: us-west-2
        bucket: staging-data

    rhoai:
      dev-cluster:
        kfp_url: https://kfp.dev.example.com/
        token: dev-token
        project_name: dev-ns
        verify_ssl: false
      staging-cluster:
        kfp_url: https://kfp.staging.example.com/
        token: staging-token
        project_name: staging-ns

    vs:
      milvus:
        local-milvus:
          host: milvus.dev.example.com
          port: 19530
          secure: false

      pgvector:
        local-pg:
          host: pgvector.dev.example.com
          port: 5432
          database: vectordb
          sslmode: prefer

    ogx:
      dev-ogx:
        base_url: https://ogx.dev.example.com
        api_key: ogx-key-123
""")


def _write_config(tmp_dir: str, content: str = _SAMPLE_YAML) -> Path:
    path = Path(tmp_dir) / ".autox.yaml"
    path.write_text(content)
    return path


def _make_args(**kwargs) -> argparse.Namespace:
    defaults = {"profile": None, "target": None}
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


# ── find_config ─────────────────────────────────────────────────────────────


class TestFindConfig:
    def test_finds_in_current_dir(self):
        with tempfile.TemporaryDirectory() as d:
            _write_config(d)
            result = find_config(d)
            assert result is not None
            assert result.name == ".autox.yaml"

    def test_finds_in_parent_dir(self):
        with tempfile.TemporaryDirectory() as d:
            _write_config(d)
            child = Path(d) / "sub" / "deep"
            child.mkdir(parents=True)
            result = find_config(child)
            assert result is not None
            assert result.parent == Path(d).resolve()

    def test_returns_none_when_missing(self):
        with tempfile.TemporaryDirectory() as d:
            result = find_config(d)
            assert result is None


# ── _interpolate ────────────────────────────────────────────────────────────


class TestInterpolate:
    def test_replaces_env_var(self):
        with patch.dict(os.environ, {"MY_KEY": "hello"}):
            assert _interpolate("${MY_KEY}") == "hello"

    def test_missing_env_var_becomes_empty(self):
        with patch.dict(os.environ, {}, clear=True):
            assert _interpolate("${NONEXISTENT}") == ""

    def test_nested_dict(self):
        with patch.dict(os.environ, {"A": "1", "B": "2"}):
            result = _interpolate({"x": "${A}", "y": {"z": "${B}"}})
            assert result == {"x": "1", "y": {"z": "2"}}

    def test_list(self):
        with patch.dict(os.environ, {"V": "val"}):
            assert _interpolate(["${V}", "plain"]) == ["val", "plain"]

    def test_non_string_passthrough(self):
        assert _interpolate(42) == 42
        assert _interpolate(True) is True


# ── load_config ─────────────────────────────────────────────────────────────


class TestLoadConfig:
    def test_loads_full_config(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write_config(d)
            cfg = load_config(path)
            assert cfg is not None
            assert cfg.default_profile == "dev"
            assert "dev" in cfg.profiles
            assert "staging" in cfg.profiles

    def test_s3_configs_parsed(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write_config(d)
            cfg = load_config(path)
            assert "minio-dev" in cfg.s3
            s3 = cfg.s3["minio-dev"]
            assert isinstance(s3, S3Config)
            assert s3.endpoint == "https://minio.dev.example.com"
            assert s3.verify_tls is False

    def test_s3_bucket_parsed(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write_config(d)
            cfg = load_config(path)
            assert cfg.s3["aws-staging"].bucket == "staging-data"

    def test_s3_bucket_defaults_to_empty(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write_config(d)
            cfg = load_config(path)
            assert cfg.s3["minio-dev"].bucket == ""

    def test_rhoai_configs_parsed(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write_config(d)
            cfg = load_config(path)
            rhoai = cfg.rhoai["dev-cluster"]
            assert isinstance(rhoai, RhoaiConfig)
            assert rhoai.project_name == "dev-ns"
            assert rhoai.verify_ssl is False

    def test_milvus_config_parsed(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write_config(d)
            cfg = load_config(path)
            m = cfg.milvus["local-milvus"]
            assert isinstance(m, MilvusConfig)
            assert m.port == 19530
            assert m.secure is False
            assert m.server_pem_path == ""  # defaults empty when omitted

    def test_milvus_server_pem_path_resolved_relative_to_config(self):
        content = textwrap.dedent("""\
            vs:
              milvus:
                secure-milvus:
                  host: milvus.dev.example.com
                  port: 19530
                  secure: true
                  server_pem_path: certs/milvus.crt
        """)
        with tempfile.TemporaryDirectory() as d:
            path = _write_config(d, content)
            cfg = load_config(path)
            m = cfg.milvus["secure-milvus"]
            assert m.secure is True
            assert m.server_pem_path == str((Path(d) / "certs/milvus.crt").resolve())

    def test_milvus_server_pem_path_absolute_preserved(self):
        abs_cert = str(Path("/etc/milvus/ca.pem"))
        content = textwrap.dedent(f"""\
            vs:
              milvus:
                secure-milvus:
                  host: milvus.dev.example.com
                  port: 19530
                  secure: true
                  server_pem_path: {abs_cert}
        """)
        with tempfile.TemporaryDirectory() as d:
            path = _write_config(d, content)
            cfg = load_config(path)
            assert cfg.milvus["secure-milvus"].server_pem_path == abs_cert

    def test_pgvector_config_parsed(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write_config(d)
            cfg = load_config(path)
            pg = cfg.pgvector["local-pg"]
            assert isinstance(pg, PgvectorConfig)
            assert pg.database == "vectordb"

    def test_ogx_config_parsed(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write_config(d)
            cfg = load_config(path)
            ogx = cfg.ogx["dev-ogx"]
            assert isinstance(ogx, OgxConfig)
            assert ogx.api_key == "ogx-key-123"

    def test_profiles_map_services(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write_config(d)
            cfg = load_config(path)
            dev = cfg.profiles["dev"]
            assert isinstance(dev, Profile)
            assert dev.s3 == "minio-dev"
            assert dev.rhoai == "dev-cluster"

    def test_returns_none_when_no_file(self):
        with patch("autox_tools.config._loader.find_config", return_value=None):
            assert load_config() is None

    def test_env_var_interpolation(self):
        yaml_with_env = textwrap.dedent("""\
            s3:
              my-s3:
                endpoint: https://s3.example.com
                access_key_id: ${TEST_AK}
                secret_access_key: ${TEST_SK}
        """)
        with tempfile.TemporaryDirectory() as d:
            path = _write_config(d, yaml_with_env)
            with patch.dict(os.environ, {"TEST_AK": "resolved-key", "TEST_SK": "resolved-secret"}):
                cfg = load_config(path)
                assert cfg.s3["my-s3"].access_key_id == "resolved-key"
                assert cfg.s3["my-s3"].secret_access_key == "resolved-secret"

    def test_invalid_section_exits(self):
        bad_yaml = textwrap.dedent("""\
            s3:
              bad:
                endpoint: https://s3.example.com
                # missing access_key_id and secret_access_key
        """)
        with tempfile.TemporaryDirectory() as d:
            path = _write_config(d, bad_yaml)
            with pytest.raises(SystemExit, match=r"Invalid s3\.bad"):
                load_config(path)


# ── resolve ─────────────────────────────────────────────────────────────────


class TestResolve:
    def test_target_overrides_profile(self):
        with tempfile.TemporaryDirectory() as d:
            _write_config(d)
            with patch("autox_tools.config._loader.find_config", return_value=Path(d) / ".autox.yaml"):
                args = _make_args(profile="dev", target="aws-staging")
                cfg = resolve("s3", args)
                assert isinstance(cfg, S3Config)
                assert cfg.endpoint == "https://s3.amazonaws.com"

    def test_profile_resolves_service(self):
        with tempfile.TemporaryDirectory() as d:
            _write_config(d)
            with patch("autox_tools.config._loader.find_config", return_value=Path(d) / ".autox.yaml"):
                args = _make_args(profile="dev")
                cfg = resolve("s3", args)
                assert isinstance(cfg, S3Config)
                assert cfg.endpoint == "https://minio.dev.example.com"

    def test_default_profile_used(self):
        with tempfile.TemporaryDirectory() as d:
            _write_config(d)
            with patch("autox_tools.config._loader.find_config", return_value=Path(d) / ".autox.yaml"), \
                 patch.dict(os.environ, {}, clear=False):
                os.environ.pop("AUTOX_PROFILE", None)
                args = _make_args()
                cfg = resolve("s3", args)
                assert isinstance(cfg, S3Config)
                assert cfg.endpoint == "https://minio.dev.example.com"

    def test_env_var_profile(self):
        with tempfile.TemporaryDirectory() as d:
            _write_config(d)
            with patch("autox_tools.config._loader.find_config", return_value=Path(d) / ".autox.yaml"), \
                 patch.dict(os.environ, {"AUTOX_PROFILE": "staging"}):
                args = _make_args()
                cfg = resolve("s3", args)
                assert isinstance(cfg, S3Config)
                assert cfg.endpoint == "https://s3.amazonaws.com"

    def test_returns_none_when_no_config_file(self):
        with patch("autox_tools.config._loader.find_config", return_value=None):
            args = _make_args(profile="dev")
            assert resolve("s3", args) is None

    def test_returns_none_when_service_not_in_profile(self):
        with tempfile.TemporaryDirectory() as d:
            _write_config(d)
            with patch("autox_tools.config._loader.find_config", return_value=Path(d) / ".autox.yaml"):
                args = _make_args(profile="staging")
                cfg = resolve("milvus", args)
                assert cfg is None

    def test_unknown_target_exits(self):
        with tempfile.TemporaryDirectory() as d:
            _write_config(d)
            with patch("autox_tools.config._loader.find_config", return_value=Path(d) / ".autox.yaml"), \
                 pytest.raises(SystemExit, match="Unknown s3 target"):
                args = _make_args(target="nonexistent")
                resolve("s3", args)

    def test_unknown_profile_exits(self):
        with tempfile.TemporaryDirectory() as d:
            _write_config(d)
            with patch("autox_tools.config._loader.find_config", return_value=Path(d) / ".autox.yaml"), \
                 pytest.raises(SystemExit, match="Unknown profile"):
                args = _make_args(profile="nonexistent")
                resolve("s3", args)

    def test_broken_profile_reference_exits(self):
        broken_yaml = textwrap.dedent("""\
            defaults:
              profile: bad
            profiles:
              bad:
                s3: does-not-exist
            s3: {}
        """)
        with tempfile.TemporaryDirectory() as d:
            _write_config(d, broken_yaml)
            with patch("autox_tools.config._loader.find_config", return_value=Path(d) / ".autox.yaml"), \
                 pytest.raises(SystemExit, match="not defined"):
                args = _make_args(profile="bad")
                resolve("s3", args)

    def test_artifacts_s3_resolves_from_s3_section(self):
        with tempfile.TemporaryDirectory() as d:
            _write_config(d)
            with patch("autox_tools.config._loader.find_config", return_value=Path(d) / ".autox.yaml"):
                args = _make_args(profile="dev")
                cfg = resolve("artifacts_s3", args)
                assert isinstance(cfg, S3Config)
                assert cfg.endpoint == "https://minio.dev.example.com"

    def test_returns_none_when_no_profile_set(self):
        yaml_no_default = textwrap.dedent("""\
            profiles:
              dev:
                s3: my-s3
            s3:
              my-s3:
                endpoint: https://s3.example.com
                access_key_id: key
                secret_access_key: secret
        """)
        with tempfile.TemporaryDirectory() as d:
            _write_config(d, yaml_no_default)
            with patch("autox_tools.config._loader.find_config", return_value=Path(d) / ".autox.yaml"), \
                 patch.dict(os.environ, {}, clear=False):
                os.environ.pop("AUTOX_PROFILE", None)
                args = _make_args()
                assert resolve("s3", args) is None
