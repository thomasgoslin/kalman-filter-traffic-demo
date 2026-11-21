# Interactive Kalman Traffic Lab

For a fresh checkout, install the Python dependencies from the [main README](README.md#run), put your local clip at the repository root as `videoplayback.mp4`, and run the baseline command there once to create `outputs/traffic-demo.detections.json`. The repository includes the demo code and example screenshots.

Double-click **Traffic Lab.cmd** in the project folder and choose Start, Refresh, Stop, or Stop and exit. You can also use PowerShell:

```powershell
./preview.ps1 start
./preview.ps1 refresh
./preview.ps1 stop
```

Open [the local preview](http://127.0.0.1:8000/). After editing the frontend, run the refresh command and reload your browser. Start builds the export if it is missing. To regenerate the four scenarios after changing the tracker, run `python build_interactive_export.py` in your usual Python environment with FFmpeg on PATH. The export uses the saved detections and lives under the Git-ignored output folder.

Press Space to play or pause from anywhere on the page. Pause and click a car to pin it, or choose its track ID. Use the timeline, the arrow keys when the video is focused, or click a time-series chart to seek. Switch between position, velocity, and image-plane trajectory in the right-hand inspector. Changing simulation clears the track selection because IDs are local to each run.

Cyan is the forecast, yellow is the measurement, and green is the Kalman correction. A detector can be online while an individual track still lacks a match. When detections first return after a global outage, unmatched old tracks are retired. The bands and ellipses describe the model's estimated uncertainty in the state.
