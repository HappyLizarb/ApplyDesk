"""A disposable JSONL helper simulation: no browser, native input, credentials or network."""

import json
import select
import sys
import time


def serve():
    """A blocking action lets tests prove that Stop bypasses the serialized request queue."""
    while line := sys.stdin.readline():
        request = json.loads(line)
        method, params = request["method"], request["params"]
        if params.get("key") == "window-closed":
            print(json.dumps({"id": request["id"], "error": "The owned window closed.", "error_code": "window_closed"}), flush=True)
            continue
        if method == "action" and params.get("text") == "wait-for-stop":
            print(json.dumps({"id": request["id"], "result": {"started": True}}), flush=True)
            continue
        if method == "action" and params.get("key") == "wait":
            ready, _, _ = select.select([sys.stdin], [], [], 5)
            if ready:
                stop = json.loads(sys.stdin.readline())
                print(json.dumps({"id": request["id"], "error": "interrupted"}), flush=True)
                print(json.dumps({"id": stop["id"], "result": {"stopped": True}}), flush=True)
                continue
        if method == "action" and params.get("key") == "fail":
            print(json.dumps({"id": request["id"], "error": "private failure " + params.get("text", "")}), flush=True)
            continue
        result = {"method": method, "text": params.get("text", ""), "pid": 42, "window_id": 88}
        if params.get("private"):
            result["action_result"] = {"private_value_matches": True}
        print(json.dumps({"id": request["id"], "result": result}), flush=True)
        if method == "close":
            return


if __name__ == "__main__":
    serve()
