"""Unit tests for the Milvus connection factory's URI handling."""

from __future__ import annotations

from autox_tools.vs.milvus._client import _build_uri


class TestBuildUri:
    def test_bare_host_secure_gets_https_scheme(self):
        uri, secure = _build_uri("milvus.example.com", 19530, True)
        assert uri == "https://milvus.example.com:19530"
        assert secure is True

    def test_bare_host_insecure_gets_http_scheme(self):
        uri, secure = _build_uri("localhost", 19530, False)
        assert uri == "http://localhost:19530"
        assert secure is False

    def test_existing_https_scheme_forces_secure(self):
        # Scheme in the host wins even if the secure flag disagrees.
        uri, secure = _build_uri("https://milvus.example.com", 19530, False)
        assert uri == "https://milvus.example.com:19530"
        assert secure is True

    def test_existing_http_scheme_forces_insecure(self):
        uri, secure = _build_uri("http://localhost", 19530, True)
        assert uri == "http://localhost:19530"
        assert secure is False

    def test_trailing_slash_and_whitespace_trimmed(self):
        uri, secure = _build_uri("  https://milvus.example.com/  ", 19530, True)
        assert uri == "https://milvus.example.com:19530"
        assert secure is True
