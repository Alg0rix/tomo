"""Small file-tool entrypoint installed in the stripped sandbox image.

Reuse the application's formatting/search/text-edit implementations without
shipping coordinator startup, database, config, secret stores or transports.
"""
from __future__ import annotations

import importlib
import json
import sys

TOOLS = {"read_file", "write_file", "str_replace", "patch", "list_dir", "search_files", "delete_file"}


def main():
    try:
        name = sys.argv[1]
        if name not in TOOLS:
            raise ValueError("Unsupported sandbox file operation")
        arguments = json.loads(sys.stdin.read(2_000_000))
        if not isinstance(arguments, dict):
            raise ValueError("Invalid sandbox file arguments")
        tool = importlib.import_module(f"app.runtime.tools.{name}")
        print(tool.run(arguments))
    except Exception:
        # Host paths and contents never enter coordinator-side exception logs.
        print("Error: sandbox file operation failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
