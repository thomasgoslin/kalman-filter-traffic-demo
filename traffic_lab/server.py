"""Loopback server for the Traffic Lab and its experiment API."""

import argparse
import json
import re
import threading
import traceback
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from uuid import uuid4

from .experiments import EXPERIMENTS, Experiments
from .workspace import OUTPUTS, ROOT


class LabServer(ThreadingHTTPServer):
    def __init__(self, port, experiments):
        super().__init__(("127.0.0.1", port), LabHandler)
        self.experiments = experiments
        self.jobs = {}
        self.job_lock = threading.Lock()
        self.active = None

    def start_run(self, settings):
        with self.job_lock:
            if self.active:
                return None
            identifier = uuid4().hex
            self.active = identifier
            self.jobs[identifier] = {"status": "running", "message": "Preparing the experiment…"}
        threading.Thread(target=self.run_experiment, args=(identifier, settings), daemon=True).start()
        return identifier

    def run_experiment(self, identifier, settings):
        def report(message):
            with self.job_lock:
                self.jobs[identifier]["message"] = message

        try:
            run = self.experiments.run(identifier, settings, report)
            result = {"status": "complete", "message": "Experiment complete.", "run": run}
        except ValueError as error:
            result = {"status": "failed", "message": str(error)}
        except Exception:
            traceback.print_exc()
            result = {"status": "failed", "message": "The experiment failed. Check the detector installation and source video."}
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

    def do_GET(self):
        path = urlsplit(self.path).path
        if not path.startswith(("/api/", "/experiments/")):
            return super().do_GET()
        if not self.local_request():
            return self.send_json(403, {"message": "Open the lab through its local URL."})
        engine = self.server.experiments
        if path == "/api/settings":
            return self.send_json(200, {
                "presets": engine.presets(), "runs": engine.saved_runs(), "active": self.server.active,
                "weights": sorted(engine.weights().keys() | {engine.detector["weights"]}),
            })
        job = re.fullmatch(r"/api/jobs/([a-f0-9]{32})", path)
        if job:
            with self.server.job_lock:
                data = self.server.jobs.get(job[1], {}).copy()
            return self.send_json(200, data) if data else self.send_json(404, {"message": "Experiment job not found."})
        if re.fullmatch(r"/experiments/[a-f0-9]{32}/(?:frames\.json|tracks-index\.json|tracks/[0-9]+\.json)", path):
            return super().do_GET()
        return self.send_json(404, {"message": "Resource not found."})

    def translate_path(self, path):
        match = re.fullmatch(r"/experiments/([a-f0-9]{32})/(frames\.json|tracks-index\.json|tracks/[0-9]+\.json)",
                             urlsplit(path).path)
        if match:
            return str(EXPERIMENTS / match[1] / match[2])
        return super().translate_path(path)

    def do_POST(self):
        if urlsplit(self.path).path != "/api/experiments":
            return self.send_json(404, {"message": "Resource not found."})
        if not self.local_request():
            return self.send_json(403, {"message": "Open the lab through its local URL."})
        if self.headers.get_content_type() != "application/json":
            return self.send_json(415, {"message": "Settings must be sent as JSON."})
        try:
            size = int(self.headers.get("Content-Length", 0))
            if not 0 < size <= 128_000:
                return self.send_json(413, {"message": "Settings request is empty or too large."})
            raw = json.loads(self.rfile.read(size))
            settings = self.server.experiments.validate(raw)
        except (KeyError, TypeError):
            return self.send_json(400, {"message": "The submitted settings are incomplete."})
        except (ValueError, OverflowError) as error:
            return self.send_json(400, {"message": str(error)})
        identifier = self.server.start_run(settings)
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
