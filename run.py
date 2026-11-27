"""Start the local Mac app; no provider calls or employer visits happen on launch."""

import argparse
import os
import sys
from pathlib import Path

from applydesk.server import AppServer
from applydesk.workspace import Workspace
from applydesk.lifecycle import LocalInstance, close_window, open_window


def main():
    """Open an isolated Chrome app window backed by a private loopback service."""
    parser = argparse.ArgumentParser(description="ApplyDesk — personal local application workspace")
    parser.add_argument("--data-dir", type=Path, default=Path.home() / "Library/Application Support/ApplyDesk")
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("ApplyDesk requires macOS.")
    os.umask(0o077)
    instance = LocalInstance(args.data_dir)
    if not instance.acquired:
        if not args.no_open and instance.existing_url():
            open_window(instance.existing_url(), instance.root)
        print("ApplyDesk is already running for this workspace.", flush=True)
        instance.close()
        return
    workspace = Workspace(args.data_dir)
    server = AppServer(workspace, args.port)
    instance.publish(server.launch_url)
    if not args.no_open:
        try:
            open_window(server.launch_url, instance.root)
        except (OSError, RuntimeError) as error:
            print("ApplyDesk's service is ready; its window could not be focused: " + str(error), flush=True)
    print("ApplyDesk running. Use Quit ApplyDesk, or press Control-C here.", flush=True)
    if args.no_open:
        print(server.launch_url, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        close_workspace(server, workspace, instance)


def close_workspace(server, workspace, instance):
    """Close the interface and release service resources even if window shutdown fails."""
    try:
        close_window(instance.root)
    finally:
        server.server_close()
        workspace.close()
        instance.close()


if __name__ == "__main__":
    main()
