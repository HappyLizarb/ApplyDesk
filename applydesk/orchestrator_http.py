"""Authenticated controls for explicit connection sources and in-app agent work."""

from .agent_contract import object_fields, string
from .providers import _validate_key
from .connection_models import CONNECTION_SOURCES, validate_provider
from .application_window import show_application


def orchestrator_action(workspace, action, data):
    """The UI session is required; external draft tokens cannot reach these controls."""
    engine = workspace.orchestrator
    if action == "show-application":
        object_fields(data, [])
        return show_application(workspace)
    if action in {"api-key", "api-key/remove", "source"}:
        return connection_action(workspace, action, data)
    if action in {"connect", "select", "disconnect", "cancel-signin", "models"}:
        return account_action(workspace, action, data)
    if action in {"settings", "role-settings"}:
        if engine.active:
            raise ValueError("Stop the agents before changing their settings.")
        save = engine.settings.save_role if action == "role-settings" else engine.settings.save
        return save(data)
    actions = {"send": engine.send, "control": engine.control, "retry": engine.retry, "reset": engine.reset}
    if action not in actions:
        raise ValueError("Unknown chat control.")
    return actions[action](data)


def connection_action(workspace, action, data):
    """Key/source changes pause queued work and invalidate older connection requests."""
    engine = workspace.orchestrator
    required = {"api-key": ["key"], "source": ["source"], "api-key/remove": []}[action]
    object_fields(data, required, ["provider"] if action != "source" else [])
    provider = validate_provider(data.get("provider", "openai"))
    if action == "api-key":
        _validate_key(data["key"])
    if action == "source" and (not isinstance(data["source"], str) or data["source"] not in CONNECTION_SOURCES):
        raise ValueError("Choose ChatGPT, OpenAI API, Anthropic API, or Google API.")
    engine.control({"paused": True})
    engine.signin.cancel()
    source = data["source"] if action == "source" else engine.accounts.connection()["source"]
    engine.accounts.select_source(source)
    if action == "api-key":
        return workspace.start(provider + "-api-key", engine.accounts.save_api_key, data["key"], engine.accounts.generation, provider)
    if action == "api-key/remove":
        return engine.accounts.remove_api_key(provider)
    return engine.accounts.state()


def account_action(workspace, action, data):
    """Keep sign-in and account management explicit, independent of API key storage."""
    engine = workspace.orchestrator
    object_fields(data, [], ["account_id"] if action in {"connect", "select"} else [])
    if action == "models":
        return workspace.start("connection-models", engine.accounts.load_models)
    if action == "cancel-signin":
        engine.signin.cancel()
        return {"status": "idle"}
    identity = string(data.get("account_id", ""), 100, empty=True)
    engine.control({"paused": True})
    engine.signin.cancel()
    if action == "disconnect":
        return workspace.start("chatgpt-disconnect", engine.accounts.disconnect)
    engine.accounts.select_source("chatgpt")
    if action == "connect":
        return engine.signin.begin(identity)
    return engine.accounts.select(identity)
