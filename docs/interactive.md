# Interactive Kalman Traffic Test

For a fresh checkout, install the Python dependencies from the [quick start](../README.md#quick-start), create the `inputs/` folder and place your clip there as `videoplayback.mp4`, and run the baseline command there once to create `outputs/traffic-demo.detections.json`. The repository includes the demo code and example screenshots.

Double-click **Traffic Lab.cmd** in the project folder and choose Start, Refresh, Stop, or Stop and exit. You can also use PowerShell:

```powershell
./preview.ps1 start
./preview.ps1 refresh
./preview.ps1 stop
```

Open [the local preview](http://127.0.0.1:8000/). After editing the frontend, run the refresh command and reload your browser. Start builds the export if it is missing. To regenerate the four scenarios after changing the tracker, run `python -m traffic_lab.export` in your usual Python environment with FFmpeg on PATH. The export uses the saved detections and lives under the Git-ignored output folder.

Press Space to play or pause in Simulation. Click a car to pin it, or choose its track ID. Use the timeline, the arrow keys when the video is focused, or click a time-series chart to seek. Switch between position, velocity, and image-plane trajectory in the right-hand inspector. Use **Expand** to open larger graphs and **Esc** to return. Changing scenario clears the track selection because IDs are local to each run. Open **Model** for the notation, equations, matrices, and scenario settings used by the export.

Cyan is the forecast, yellow is the measurement, and green is the Kalman correction. A detector can be online while an individual track still lacks a match. When detections first return after a global outage, unmatched old tracks are retired. The bands and ellipses describe the model's estimated uncertainty in the state.

## Run a custom experiment

Open **Settings**, choose a starting preset, and edit the tracker, detector, outage, measurement noise, or display parameters. Expand the matrix section to edit F, H, the per-frame offset, the covariances, or the initial velocity. **Reset to preset** restores the starting values. **Run experiment** computes a separate result, which can be opened in Simulation alongside the standard scenarios.

The launcher starts the local experiment server. You can also run `python -m traffic_lab.server` from the project root after building the export. A matching detector configuration reuses cached detections. To change detector weights for a fresh pass, place a trusted local YOLO weights file in the inputs folder. Saved experiments and their caches remain under the ignored outputs directory.
