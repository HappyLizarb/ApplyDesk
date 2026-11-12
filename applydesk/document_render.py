"""Render previews with isolated bundled LibreOffice and Poppler processes."""

from pathlib import Path
import subprocess
import tempfile

from pypdf import PdfReader

RUNTIME = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies"
SOFFICE = RUNTIME / "bin/override/soffice"
PDFTOPPM = RUNTIME / "bin/override/pdftoppm"


def run_renderer(command):
    # Never interpolate document names into a shell command.
    try:
        result = subprocess.run([str(item) for item in command], capture_output=True,
                                text=True, timeout=90, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"Document renderer is unavailable: {error}") from error
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()[-1200:]
        raise RuntimeError(f"Document rendering failed: {detail}")


def render_docx(source, directory):
    # Separate office profiles avoid profile locks when two documents render together.
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="applydesk-office-") as profile:
        run_renderer([SOFFICE, f"-env:UserInstallation={Path(profile).as_uri()}",
                      "--headless", "--convert-to", "pdf", "--outdir", directory, source])
    result = directory / (source.stem + ".pdf")
    if not result.is_file() or not result.stat().st_size:
        raise RuntimeError("LibreOffice did not produce a PDF preview.")
    return result, render_pdf(result, directory)


def render_pdf(source, directory):
    # Bound preview expansion; a valid PDF must render every page for later review.
    page_count = len(PdfReader(source).pages)
    if page_count < 1 or page_count > 40:
        raise ValueError("PDF previews require between 1 and 40 pages.")
    directory.mkdir(parents=True, exist_ok=True)
    run_renderer([PDFTOPPM, "-scale-to", "1800", "-png", source, directory / "page"])
    images = sorted(directory.glob("page-*.png"), key=lambda path: int(path.stem.split("-")[-1]))
    if len(images) != page_count:
        raise RuntimeError("Some PDF preview pages did not render.")
    return images
