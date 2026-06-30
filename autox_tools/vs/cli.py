"""Unified vector store CLI dispatcher.

Routes ``uv run vs <backend> <command> ...`` to the appropriate backend module.

Supported backends:

- ``milvus``   -- Milvus vector database
- ``pgvector`` -- PostgreSQL with pgvector extension

Usage::

    uv run vs milvus list [--counts] [--json]
    uv run vs pgvector health [--json]
"""

from __future__ import annotations

import importlib
import sys

_BACKENDS: dict[str, str] = {
    "milvus": "autox_tools.vs.milvus.cli",
    "pgvector": "autox_tools.vs.pgvector.cli",
}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("Usage: vs <backend> <command> [options]")
        print()
        print("Backends:")
        for name in sorted(_BACKENDS):
            print(f"  {name:<12} Manage {name} vector database")
        print()
        print("Run 'vs <backend> --help' for backend-specific commands.")
        if len(sys.argv) >= 2:
            return
        sys.exit(2)

    backend = sys.argv[1]
    if backend not in _BACKENDS:
        sys.exit(f"Unknown backend '{backend}'. Choose from: {', '.join(sorted(_BACKENDS))}")

    prog = f"vs {backend}"
    sys.argv = [prog, *sys.argv[2:]]

    mod = importlib.import_module(_BACKENDS[backend])
    mod.main(prog=prog)


if __name__ == "__main__":
    main()
