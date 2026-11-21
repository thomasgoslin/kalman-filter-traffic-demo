"""Project paths, output publication, and source fingerprints."""

import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VIDEO = Path("inputs/videoplayback.mp4")
if not (ROOT / VIDEO).is_file() and (ROOT / VIDEO.name).is_file():
    VIDEO = Path(VIDEO.name)
OUTPUTS = Path("outputs")
CACHE = OUTPUTS / "traffic-demo.detections.json"
RENDERER = Path("traffic_lab/render.py")
PIPELINE_FILES = (
    RENDERER, Path("traffic_lab/tracker.py"), Path("traffic_lab/scenarios.py"),
    Path("traffic_lab/media.py"), Path("traffic_lab/overlay.py"),
)


def digest(path):
    with (ROOT / path).open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def pipeline_hashes():
    return {path.as_posix(): digest(path) for path in PIPELINE_FILES}


def replace_outputs(stage, backup):
    backup.mkdir()
    previous = []
    installed = []

    try:
        for source in sorted(stage.iterdir()):
            target = ROOT / OUTPUTS / source.name

            if target.exists() or target.is_symlink():
                target.rename(backup / source.name)
                previous.append(target)

            source.rename(target)
            installed.append(target)

    except (OSError, KeyboardInterrupt):
        for target in reversed(installed):
            target.rename(stage / target.name)

        for target in reversed(previous):
            (backup / target.name).rename(target)

        raise
