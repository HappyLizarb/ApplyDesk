"""Local chat client: authenticated draft requests with no provider API key."""

import argparse
from http.client import HTTPConnection
import json
from pathlib import Path
import sys
from urllib.parse import urlencode, urlsplit


class AgentClient:
    """Read the app-owned connection privately and send only allowlisted local requests."""

    def __init__(self, root):
        # The app rotates this token at startup; it is never printed or sent to a provider.
        path = Path(root) / "agent.connection.json"
        if path.is_symlink() or path.stat().st_mode & 0o077:
            raise ValueError("The chat connection file must be private to your Mac account.")
        connection = json.loads(path.read_text())
        origin = urlsplit(connection["origin"])
        if origin.scheme != "http" or origin.hostname != "127.0.0.1" or origin.username or origin.path:
            raise ValueError("Invalid local app address.")
        self.port, self.token = origin.port, connection["token"]

    def request(self, action, payload=None, query=None):
        """Keep secrets out of arguments, output, proxies, and redirect destinations."""
        allowed = {"guide", "tasks", "task", "asset", "enqueue", "draft", "block", "capture"}
        if action not in allowed:
            raise ValueError("Unknown chat action.")
        path = "/agent/v1/" + action + ("?" + urlencode(query) if query else "")
        connection = HTTPConnection("127.0.0.1", self.port, timeout=120)
        headers = {"Authorization": "Bearer " + self.token, "Content-Type": "application/json"}
        try:
            connection.request("POST" if payload is not None else "GET", path,
                               body=json.dumps(payload) if payload is not None else None, headers=headers)
            response = connection.getresponse()
            content = response.read()
            if response.status != 200:
                raise ValueError(json.loads(content).get("error", "Local chat request failed."))
            return content if action == "asset" else json.loads(content)
        finally:
            connection.close()


def main():
    """Read mutation payloads from files, preventing accidental shell interpolation of text."""
    parser = argparse.ArgumentParser(description="ApplyDesk chat-only draft interface")
    parser.add_argument("action", choices=["guide", "tasks", "task", "asset", "enqueue", "draft", "block", "capture"])
    parser.add_argument("--data-dir", type=Path, default=Path.home() / "Library/Application Support/ApplyDesk")
    parser.add_argument("--id", help="Task ID for task or asset")
    parser.add_argument("--path", help="Task-bound image path for asset")
    parser.add_argument("--input", type=Path, help="JSON file for a write operation")
    parser.add_argument("--output", type=Path, help="Save returned JSON or a page image locally")
    args = parser.parse_args()
    try:
        write = args.action in {"enqueue", "draft", "block", "capture"}
        if write != bool(args.input) or (args.action in {"task", "asset"} and not args.id):
            raise ValueError("Write operations require --input; task and asset require --id.")
        if args.action == "asset" and (not args.path or not args.output):
            raise ValueError("Asset requires --path and --output.")
        payload = json.loads(args.input.read_text()) if args.input else None
        query = {"id": args.id, **({"path": args.path} if args.path else {})} if args.id else None
        result = AgentClient(args.data_dir).request(args.action, payload, query)
        content = result if isinstance(result, bytes) else json.dumps(result, indent=2, ensure_ascii=False).encode()
        if args.output:
            args.output.write_bytes(content)
            args.output.chmod(0o600)
            print("Saved requested task output.")
        else:
            print(content.decode())
    except (OSError, ValueError, KeyError) as exc:
        print("ApplyDesk: " + str(exc), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
