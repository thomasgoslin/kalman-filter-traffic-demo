# Kalman filter video demo

**For the VU Data Assimilation seminar on Reich and Cotter, §6.1.** Build a short video that shows how a Kalman filter combines a motion forecast with bounding boxes from an object detector. The presenter should be able to pause on a frame, identify the detector box, forecast box, and corrected box, and explain why the correction lands where it does.

**Status** — **01.1 verified. 01.2 implemented and checked with synthetic video.** The phase acceptance gate remains open until the user supplies the clip and the real detector and seminar MP4 are verified on it. Resume at **01.2**.

## Scope and deliverables

This is a compact offline demo with one end-to-end milestone. Use a single cyclist for the clearest presentation, while supporting multiple objects in the input clip. The supplied clip determines which object categories to detect.

- Keep the Python code in `kalman_filter_demo.py` at the demo root. Give it a command-line input video and output MP4 path.
- Use a pretrained detector to produce bounding boxes. A small Ultralytics YOLO model in **prediction mode** is a suitable default. Use its class IDs, confidence scores, and boxes in original-frame pixel coordinates. Save detections once so overlay edits and filter tuning can reuse the same measurements. [Ultralytics detection API](https://docs.ultralytics.com/tasks/detect/)
- Present this as a teaching implementation inspired by NVIDIA's NvSORT. NvSORT also combines a constant-velocity Kalman model with detection-to-track matching. [NVIDIA's tracker documentation](https://docs.nvidia.com/deepstream/9.2/text/DS_plugin_gst-nvtracker#nvsort-tracker)

## Filter and data contract

| Quantity | Definition |
|---|---|
| $x_k$ | Track state $(c_x,c_y,w,h,v_x,v_y)^\top$, with box center, width, height, and center velocity. Units are pixels and pixels per frame. |
| $y_k$ | The matched detector box $(c_x,c_y,w,h)^\top$. Convert from corner coordinates if needed. |
| $F$ | Advance the center by one frame of velocity. Carry width, height, and velocity forward. |
| $H$ | Select the first four state components, the ones the detector measures. |
| $Q,R,P_0$ | Process, measurement, and initial state covariances. Expose them as named settings in the script. Initialize velocity to zero with enough uncertainty to learn it from later detections. Use $R$ to represent box jitter. Detector confidence serves as a detection threshold. |

For each frame, predict each existing track with $\hat x^-=F\hat x$ and $P^-=FPF^\top+Q$. Save the forecast box **before** correction. Use class-compatible intersection over union (IoU) between forecast boxes and detections for one-to-one matching. Minimize $1-\mathrm{IoU}$ with `scipy.optimize.linear_sum_assignment`. Give pairs below the overlap threshold or from different classes a high penalty, then discard any such pairs in the assignment. [SciPy assignment documentation](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.linear_sum_assignment.html)

For each matched pair, compute $r=y_k-H\hat x^-$, $S=HP^-H^\top+R$, and $K=P^-H^\top S^{-1}$ using a linear solve. Apply $\hat x=\hat x^-+Kr$ and the covariance update $P=(I-KH)P^-(I-KH)^\top+KRK^\top$. Draw the corrected box from $H\hat x$. Start a new track from an unmatched detection. For unmatched tracks, carry the forecast forward as the current estimate and retire the track after a configurable number of consecutive misses. Simple overlap matching can swap IDs when similar objects cross or remain hidden.

Use one detection-cache record per frame, including frames with an empty list. Store the frame index, class ID, confidence, and four box coordinates per detection. Validate that the cache matches the input video. For a controlled gap in the talk, add a render-time option to withhold detections over a specified frame range and label those frames **Detection withheld**.

## Implementation tickets

### 01.1 — Build and verify the tracker

Implement the filter and association in the standalone Python file. Keep detector inference outside the filter so the tracker can accept synthetic measurements or cached detections.

**Acceptance checks**

- In a scalar position check, a forecast of 120 px, a reading of 116 px, and a gain of 0.5 produce 118 px.
- On two synthetic tracks with distinct trajectories, permuting the order of detections preserves track IDs. An unmatched track predicts through a short gap and expires after the configured limit. Also check empty frames and first-frame initialization.
- The saved forecast and corrected boxes differ on a frame with a nonzero innovation and a positive position gain.

### 01.2 — Connect detection and render the seminar clip

After **01.1**, run the detector on the supplied clip, cache its boxes, and render the input video with three colors for **Forecast**, **Detector**, and **Kalman update**. Use the blue forecast box during a miss and the yellow detector box for a new track. Label each track with its ID. The implementation uses Pillow for overlays and the workspace's FFmpeg and ffprobe for video I/O. It exports a silent MP4 at the source's nominal frame rate.

**Acceptance checks**

- On the user’s clip, verify at least one matched frame where all three boxes are distinguishable and one short measurement gap where the forecast advances. If you control the gap, confirm the overlay labels it.
- Replay from cached detections and confirm it gives the same tracking results for the same settings. Record the two frame numbers worth pausing on and whether the gap was natural or controlled.
- Open the MP4 and inspect those frames at presentation size. This is the first usable release. Until the input clip arrives, record real-video verification as pending.

## Run the demo

Use Python 3.11 or later with NumPy, SciPy, and Pillow 10.1 or later. A fresh detector run also needs Ultralytics and compatible detection weights. The default uses `yolo26n.pt`. Make FFmpeg and ffprobe available on PATH.

```powershell
python -m pip install numpy scipy "Pillow>=10.1" ultralytics
python kalman_filter_demo.py cyclist.mp4 outputs/cyclist-demo.mp4
```

The first run writes `outputs/cyclist-demo.detections.json` alongside the MP4. To render a second cut without repeating inference, pass `--cache outputs/cyclist-demo.detections.json` with the same detector settings and source video. Use `--drop-frames 90:93` to withhold detections on zero-based frames 90, 91, and 92. The cache keeps the original detections for those frames. Use `--help` for the detector and track settings.

Add `--frames-dir outputs/traffic-demo-frames` to export every annotated frame as a PNG alongside the normal MP4 export. Choose a new folder. The filenames use the same zero-based index as the on-image label, so frame 341 is `frame_000341.png`.

## Verification record

- **01.1 passed.** The scalar correction produced 118 px with posterior variance 0.5. Synthetic tracks passed initialization, reversed detection order, class gating, prediction during a gap, recovery, and retirement checks.
- **01.2 synthetic integration passed.** A simulated detector drove two moving tracks over a 10-frame video. FFmpeg preserved the frame count and 10 fps. Cache replay produced identical decoded frames. Withholding frames 5–6 changed the render without changing cached detections, and a mismatched detector setting was rejected. The three-box overlay was inspected at frame 3.
- **01.2 real-video check.** Ultralytics processed the traffic clip and the render contained all 716 frames. The three-box overlay was inspected on the red car (ID 16) at frame 341, about 00:11.38. A short measurement-gap example on the traffic clip remains to be selected.
