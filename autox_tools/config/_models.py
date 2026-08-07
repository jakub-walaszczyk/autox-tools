"""Typed configuration models for autox-tools service connections."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class S3Config:
    endpoint: str
    access_key_id: str
    secret_access_key: str
    region: str = "us-east-1"
    verify_tls: bool = True
    bucket: str = ""


@dataclass(frozen=True)
class RhoaiConfig:
    kfp_url: str
    token: str
    project_name: str
    verify_ssl: bool = True
    k8s_api_url: str = ""
    k8s_api_port: str = ""


@dataclass(frozen=True)
class MilvusConfig:
    host: str
    port: int
    user: str = ""
    password: str = ""
    secure: bool = False
    # Path to the server/CA PEM certificate for one-way TLS. Passed through to
    # pymilvus' ``server_pem_path``. Ignored when ``secure`` is False.
    server_pem_path: str = ""


@dataclass(frozen=True)
class PgvectorConfig:
    host: str
    port: int
    database: str
    user: str = ""
    password: str = ""
    sslmode: str = "prefer"


@dataclass(frozen=True)
class OgxConfig:
    base_url: str
    api_key: str = ""


@dataclass(frozen=True)
class MaasConfig:
    # MaaS host root, without any API path (e.g. "https://maas.apps.<cluster>").
    # The listing endpoint and per-model inference endpoints are derived from it.
    base_url: str
    api_key: str = ""
    # Set False for clusters exposing self-signed TLS routes.
    verify_tls: bool = True


@dataclass(frozen=True)
class Profile:
    s3: str = ""
    artifacts_s3: str = ""
    rhoai: str = ""
    milvus: str = ""
    pgvector: str = ""
    ogx: str = ""
    maas: str = ""


SERVICE_CONFIG_TYPES: dict[str, type] = {
    "s3": S3Config,
    "artifacts_s3": S3Config,
    "rhoai": RhoaiConfig,
    "milvus": MilvusConfig,
    "pgvector": PgvectorConfig,
    "ogx": OgxConfig,
    "maas": MaasConfig,
}

_SERVICE_ATTRS: dict[str, str] = {
    "s3": "s3",
    "artifacts_s3": "s3",
    "rhoai": "rhoai",
    "milvus": "milvus",
    "pgvector": "pgvector",
    "ogx": "ogx",
    "maas": "maas",
}

_SERVICE_YAML_SECTIONS: dict[str, str] = {
    "s3": "s3",
    "artifacts_s3": "s3",
    "rhoai": "rhoai",
    "milvus": "vs.milvus",
    "pgvector": "vs.pgvector",
    "ogx": "ogx",
    "maas": "maas",
}


@dataclass
class AutoxConfig:
    default_profile: str = ""
    profiles: dict[str, Profile] = field(default_factory=dict)
    s3: dict[str, S3Config] = field(default_factory=dict)
    rhoai: dict[str, RhoaiConfig] = field(default_factory=dict)
    milvus: dict[str, MilvusConfig] = field(default_factory=dict)
    pgvector: dict[str, PgvectorConfig] = field(default_factory=dict)
    ogx: dict[str, OgxConfig] = field(default_factory=dict)
    maas: dict[str, MaasConfig] = field(default_factory=dict)

    def service_configs(self, service_type: str) -> dict:
        attr = _SERVICE_ATTRS.get(service_type, service_type)
        return getattr(self, attr, {})
