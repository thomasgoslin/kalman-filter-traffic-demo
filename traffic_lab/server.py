"""Loopback server for the Traffic Lab and its experiment API."""

import argparse
import json
import re
import threading
import traceback
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import parse_qs, unquote, urlsplit
from uuid import uuid4

from .experiments import EXPERIMENTS, VIDEO_LIBRARY, Experiments
from .videos import MAX_UPLOAD_BYTES, available_videos, prepare_video
from .workspace import OUTPUTS, ROOT


class LabServer(ThreadingHTTPServer):
    def __init__(self, port, experiments):
        super().__init__(("127.0.0.1", port), LabHandler)
        self.experiments = experiments
        self.jobs = {}
        self.job_lock = threading.Lock()
        self.active = None

    def start_job(self, work, message, *, before_start=None):
        with self.job_lock:
            if self.active:
                return None
            if before_start is not None:
                before_start()
            identifier = uuid4().hex
            self.active = identifier
            self.jobs[identifier] = {"status": "running", "message": message}
        threading.Thread(target=self.run_job, args=(identifier, work), daemon=True).start()
        return identifier

    def run_job(self, identifier, work):
        def report(message):
            with self.job_lock:
                self.jobs[identifier]["message"] = message

        try:
            result = {"status": "complete", **work(identifier, report)}
        except ValueError as error:
            result = {"status": "failed", "message": str(error)}
        except Exception:
            traceback.print_exc()
            result = {"status": "failed", "message": "Processing failed. Check the detector installation and source video."}
        with self.job_lock:
            self.jobs[identifier] = result
            self.active = None


class LabHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT / OUTPUTS / "interactive", **kwargs)

    def list_directory(self, path):
        self.send_error(404)

    def send_json(self, status, data):
        body = json.dumps(data, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def local_request(self):
        port = self.server.server_port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        host = self.headers.get("Host")
        origin = self.headers.get("Origin")
        return host in hosts and (origin is None or origin == f"http://{host}")

    def source_engine(self):
        source_id = parse_qs(urlsplit(self.path).query).get("video", ["default"])[0]
        if source_id == "default":
            return self.server.experiments
        if re.fullmatch(r"[a-f0-9]{32}", source_id):
            try:
                return Experiments(source_id)
            except FileNotFoundError:
                pass
        raise ValueError("Video not found.")

    def do_GET(self):
        path = urlsplit(self.path).path
        if not path.startswith(("/api/", "/experiments/", "/videos/")):
            return super().do_GET()
        if not self.local_request():
            return self.send_json(403, {"message": "Open the lab through its local URL."})
        if path == "/api/settings":
            try:
                engine = self.source_engine()
            except ValueError as error:
                return self.send_json(404, {"message": str(error)})
            return self.send_json(200, {
                "presets": engine.presets(), "runs": engine.saved_runs(), "active": self.server.active,
                "weights": sorted(engine.weights().keys() | {engine.detector["weights"]}),
                "videos": available_videos(),
            })
        job = re.fullmatch(r"/api/jobs/([a-f0-9]{32})", path)
        if job:
            with self.server.job_lock:
                data = self.server.jobs.get(job[1], {}).copy()
            return self.send_json(200, data) if data else self.send_json(404, {"message": "Job not found."})
        if self.media_path(path):
            return super().do_GET()
        return self.send_json(404, {"message": "Resource not found."})

    def do_HEAD(self):
        path = urlsplit(self.path).path
        if path.startswith(("/experiments/", "/videos/")) and (
            not self.local_request() or not self.media_path(path)
        ):
            return self.send_error(404)
        return super().do_HEAD()

    def media_path(self, path):
        match = re.fullmatch(r"/experiments/([a-f0-9]{32})/(frames\.json|tracks-index\.json|tracks/[0-9]+\.json)", path)
        if match:
            return EXPERIMENTS / match[1] / match[2]
        match = re.fullmatch(r"/videos/([a-f0-9]{32})/(source\.mp4|manifest\.json|[a-z0-9-]+/(?:frames\.json|tracks-index\.json|tracks/[0-9]+\.json))", path)
        if match:
            return VIDEO_LIBRARY / match[1] / match[2]
        return None

    def translate_path(self, path):
        route = urlsplit(path).path
        if route.rstrip("/") in {"/simulation", "/model", "/settings"}:
            return super().translate_path("/index.html")
        media = self.media_path(route)
        return str(media) if media else super().translate_path(path)

    def do_DELETE(self):
        match = re.fullmatch(r"/api/experiments/([a-f0-9]{32})", urlsplit(self.path).path)
        if not match:
            return self.send_json(404, {"message": "Experiment not found."})
        if not self.local_request():
            return self.send_json(403, {"message": "Open the lab through its local URL."})
        try:
            engine = self.source_engine()
            with self.server.job_lock:
                if self.server.active:
                    return self.send_json(409, {"message": "Wait for the current job to finish."})
                deleted = engine.delete(match[1])
        except ValueError as error:
            return self.send_json(404, {"message": str(error)})
        if not deleted:
            return self.send_json(404, {"message": "Saved experiment not found for this video."})
        return self.send_json(200, {"message": "Experiment deleted."})

    def upload(self):
        label = unquote(self.headers.get("X-Video-Name", ""))
        if (not label.lower().endswith(".mp4") or len(label) > 255
                or any(char in label for char in "/\\") or not label.isprintable()):
            return self.send_json(400, {"message": "Choose a file with an .mp4 extension."})
        try:
            size = int(self.headers.get("Content-Length", 0))
        except ValueError:
            size = 0
        if not 0 < size <= MAX_UPLOAD_BYTES:
            return self.send_json(413, {"message": "Choose an MP4 of at most 100 MB."})
        if self.server.active:
            return self.send_json(409, {"message": "Wait for the current job to finish."})
        VIDEO_LIBRARY.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(prefix=".upload-", suffix=".mp4", dir=VIDEO_LIBRARY, delete=False) as incoming:
            upload = Path(incoming.name)
            try:
                remaining = size
                while remaining:
                    data = self.rfile.read(min(remaining, 1024 * 1024))
                    if not data:
                        raise ValueError("The upload was interrupted.")
                    incoming.write(data)
                    remaining -= len(data)
            except (OSError, ValueError):
                incoming.close()
                upload.unlink(missing_ok=True)
                return self.send_json(400, {"message": "The upload was interrupted."})

        def process(identifier, report):
            try:
                video = prepare_video(identifier, upload, label, self.server.experiments, report)
                return {"message": "Video ready.", "video": video}
            finally:
                upload.unlink(missing_ok=True)

        job = self.server.start_job(process, "Preparing your video…")
        if job is None:
            upload.unlink(missing_ok=True)
            return self.send_json(409, {"message": "Wait for the current job to finish."})
        return self.send_json(202, {"job": job})

    def do_POST(self):
        if not self.local_request():
            return self.send_json(403, {"message": "Open the lab through its local URL."})
        path = urlsplit(self.path).path
        if path == "/api/videos":
            return self.upload()
        if path != "/api/experiments":
            return self.send_json(404, {"message": "Resource not found."})
        if self.headers.get_content_type() != "application/json":
            return self.send_json(415, {"message": "Settings must be sent as JSON."})
        try:
            size = int(self.headers.get("Content-Length", 0))
            if not 0 < size <= 128_000:
                return self.send_json(413, {"message": "Settings request is empty or too large."})
            raw = json.loads(self.rfile.read(size))
            engine = self.source_engine()
            settings = engine.validate(raw)
        except (KeyError, TypeError):
            return self.send_json(400, {"message": "The submitted settings are incomplete."})
        except (ValueError, OverflowError) as error:
            return self.send_json(400, {"message": str(error)})
        def work(identifier, report):
            return {"message": "Experiment complete.", "run": engine.run(identifier, settings, report)}

        try:
            identifier = self.server.start_job(
                work, "Preparing the experiment…",
                before_start=lambda: engine.ensure_unique_name(settings["label"]),
            )
        except ValueError as error:
            return self.send_json(409, {"message": str(error)})
        if identifier is None:
            return self.send_json(409, {"message": "An experiment is already running."})
        return self.send_json(202, {"job": identifier})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    with LabServer(args.port, Experiments()) as server:
        print(f"Traffic Lab is running at http://127.0.0.1:{server.server_port}/", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
