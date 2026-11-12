"""Stage unused files so manifest failures cannot destroy a still-referenced upload."""

import json
import shutil


class DeletionRecoveryError(RuntimeError):
    """Keep references detached when a staged operation requires startup recovery."""


def _cleanup(directory):
    """Report incomplete physical cleanup without undoing a committed library deletion."""
    try:
        if directory.exists():
            shutil.rmtree(directory)
        return False
    except OSError:
        return True


def _committed(service, identity):
    """The durable manifest decides whether a failed write can still be rolled back."""
    try:
        records = json.loads(service.manifest_path.read_text())
    except (OSError, ValueError) as error:
        raise DeletionRecoveryError("File removal was interrupted. Restart ApplyDesk to recover it.") from error
    service.manifest = records
    return identity not in records


def remove_unused(service, identity):
    """Rename first, commit metadata second, and only then begin irreversible byte cleanup."""
    directory = service.root / identity
    staged = service.root / (".deleting-" + identity)
    if directory.is_symlink() or directory.resolve().parent != service.root or staged.exists() or staged.is_symlink():
        raise ValueError("Invalid document directory or unfinished file removal.")
    if directory.exists():
        directory.rename(staged)
    try:
        service._write_manifest({key: value for key, value in service.manifest.items() if key != identity})
    except BaseException:
        if not _committed(service, identity):
            try:
                if staged.exists():
                    staged.rename(directory)
            except OSError as error:
                raise DeletionRecoveryError("File removal was interrupted. Restart ApplyDesk to recover it.") from error
            raise
    return _cleanup(staged)


def recover_deletions(root, manifest):
    """Restore precommit files or retry postcommit cleanup after an interrupted app session."""
    for staged in root.glob(".deleting-*"):
        if staged.is_symlink() or not staged.is_dir():
            continue
        identity = staged.name.removeprefix(".deleting-")
        directory = root / identity
        if identity not in manifest:
            _cleanup(staged)
        elif not directory.exists():
            try:
                staged.rename(directory)
            except OSError:
                continue
