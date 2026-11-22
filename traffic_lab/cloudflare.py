"""Build and preview the fixed-video cloudflare website."""

import argparse
import shutil
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from zipfile import ZIP_DEFLATED, ZipFile

from .experiments import Experiments, read_json
from .export import build_interactive, write_json
from .workspace import OUTPUTS, ROOT, replace_outputs


CLOUDFLARE_OUTPUT = ROOT / OUTPUTS / "cloudflare"
FIXTURE = ROOT / "cloudflare_assets"
SIMULATOR_MODULES = ("__init__", "experiments", "export", "media", "scenarios", "tracker", "workspace")
ASSET_LIMIT = 25 * 1024 * 1024


def build():
    video = FIXTURE / "source.mp4"
    cache = FIXTURE / "detections.json"
    metadata = read_json(cache)["video"]
    (ROOT / OUTPUTS).mkdir(exist_ok=True)
    with TemporaryDirectory(prefix=".cloudflare-", dir=ROOT / OUTPUTS) as temporary:
        work = Path(temporary)
        stage = work / "new"
        site = stage / "cloudflare"
        build_interactive(site, video, cache, video_metadata=metadata)
        shutil.copyfile(cache, site / "detections.json")
        engine = Experiments(hosted_dir=site)
        for asset in (ROOT / "cloudflare_web").glob("*.js"):
            shutil.copyfile(asset, site / asset.name)
        page = site / "index.html"
        html = page.read_text(encoding="utf-8")
        html = html.replace('<html lang="en">', '<html lang="en" data-cloudflare>', 1)
        html = html.replace("on the selected video. Each result appears as a separate scenario.",
                            "on the shared traffic clip. Custom runs are saved in this browser.", 1)
        page.write_text(html, encoding="utf-8")
        write_json(site / "cloudflare-settings.json", {
            "presets": engine.presets(), "weights": [engine.detector["weights"]],
            "videos": [{"id": "default", "label": "Built-in traffic clip"}],
            "source": engine.source_hash,
        })
        with ZipFile(site / "simulator.zip", "w", ZIP_DEFLATED) as bundle:
            for module in SIMULATOR_MODULES:
                path = ROOT / "traffic_lab" / (module + ".py")
                bundle.write(path, path.relative_to(ROOT).as_posix())
        for path in site.rglob("*"):
            if path.is_file() and path.stat().st_size > ASSET_LIMIT:
                raise ValueError(f"{path.name} exceeds Cloudflare's 25 MiB limit per static asset.")
        replace_outputs(stage, work / "previous")
    print(f"Cloudflare build ready in {CLOUDFLARE_OUTPUT.relative_to(ROOT)}.")


class CloudflarePreview(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=CLOUDFLARE_OUTPUT, **kwargs)

    def translate_path(self, path):
        if urlsplit(path).path.rstrip("/") in {"/simulation", "/model", "/settings"}:
            path = "/index.html"
        return super().translate_path(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("build", "serve"))
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    if args.action == "build":
        build()
    else:
        if not (CLOUDFLARE_OUTPUT / "index.html").is_file():
            parser.error("Build the cloudflare site first.")
        with ThreadingHTTPServer(("127.0.0.1", args.port), CloudflarePreview) as server:
            print(f"Cloudflare mode at http://127.0.0.1:{server.server_port}/", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass


if __name__ == "__main__":
    main()
