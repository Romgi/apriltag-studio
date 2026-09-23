# User guide

[Project home](../README.md) · [Calibration](CALIBRATION.md) · [3D pose](3D_POSE.md)

## Start a camera session

1. Connect the USB webcam and close other applications using it.
2. Launch **Start AprilTag Studio.cmd**, or run `app.py` from the source environment.
3. Select the camera. **Refresh cameras** updates the device list.
4. Start with **1920 × 1080 / 30 FPS**, **DSHOW**, **MJPG**, **tag36h11**, and **Full detail**.
5. Click **Start camera**. Hold a tag flat with its entire border visible, and select its inspector row or its live 3D plate to inspect it.

The default **Split** workspace shows the image and 3D scene together. Choose **Camera** or **3D Pose** for a larger single view. Pixel detection works without calibration; distance and live 3D geometry require the [calibration workflow](CALIBRATION.md).

Open [tag36h11-samples.html](../assets/tag36h11-samples.html) in a browser to print IDs 0 and 1. Use **100% / actual size**, with browser headers and footers disabled. The black tag square should measure **120 mm**; verify it with a ruler. The white margin is needed for detection but is excluded from the tag edge measurement.

## Camera modes and settings

Camera modes are requests to the driver. **Resolution** reports the image dimensions received by the application; **Camera FPS** measures successful reads. A driver may advertise or accept a setting that differs from the images actually delivered.

| Control | Use |
| --- | --- |
| 1920 × 1080 / 30 FPS | Starting point for smaller or more distant tags when supported. |
| 1280 × 720 / 60 FPS | Try for motion and responsiveness if the camera delivers this rate. |
| Custom | Enter a requested width, height, and FPS supported by your camera. |
| DSHOW / MSMF | Alternative Windows camera backends; driver behavior can differ. |
| MJPG / YUY2 / Default | Request a USB video format, or let the driver choose. |
| Auto exposure / Manual exposure | Adjust exposure when the camera driver supports these controls. |
| Autofocus | Request driver autofocus. Lock focus for repeatable calibration when possible. |

Stop capture before changing camera controls. Exposure and autofocus requests can be ignored by a driver. Shorter exposures can reduce motion blur, but require sufficient light.

## Detector controls

Choose the family matching the printed tags, change settings, and click **Apply detection settings**. Supported families are `tag36h11`, `tag16h5`, `tag25h9`, `tagCircle21h7`, `tagCircle49h12`, `tagStandard41h12`, `tagStandard52h13`, and `tagCustom48h12`.

| Control | Meaning |
| --- | --- |
| Full detail / decimation 1.0 | Full-resolution quadrilateral detection; a useful starting point for small tags. |
| Balanced / 1.5 or Speed / 2.0 | Less detector work, with a possible loss of small or distant tags. |
| CPU threads | Up to 8 by default. Use **Benchmark CPU threads** to compare settings. |
| Blur sigma | Optional image smoothing; default 0. |
| Decode sharpen | Decoding sharpening parameter; default 0.25. |
| Min. margin | Rejects detections below a decoding margin; default 20. |
| Max corrected bits | Allowed Hamming corrections; default 0. Allowing 1 or 2 can admit less reliable decodes. |

Change one quality control at a time and compare detections in the same scene. Decimation trades speed against detection distance; CPU threads are another upstream tuning control. [AprilRobotics tuning guidance](https://github.com/AprilRobotics/apriltag#tuning-the-detector-parameters).

## Read the measurements

| Readout | Interpretation |
| --- | --- |
| Camera FPS | Successful camera reads per second, averaged over recent frames. |
| Detection FPS | Completed processed frames per second, averaged over recent results. It is not simply 1000 divided by detection time. |
| Detect time | Image conversion, detection, filtering, and optional pose time for one frame. |
| Capture-to-result | Time from receiving a frame in the app to completing its result. Sensor exposure, USB/driver buffering, and display presentation delays are excluded. |
| Frames skipped | Cumulative captured frames bypassed since this stream started so the detector can stay current. |
| System CPU / app CPU | Overall CPU load and this application's share of the PC's logical CPU capacity. |
| Decision margin | A native decoding/contrast score, **not a confidence percentage**. |
| Corrected bits | Hamming correction count used to decode the tag. |
| Center / corners | Original camera-image coordinates: x right, y down, in pixels. |
| Area | Area of the detected four-corner polygon in square pixels. |
| Image rotation | Angle of canonical corner 0 → corner 1 against image +x. Positive angles run clockwise on screen; this is distinct from 3D yaw. |

Corner indices follow the decoded tag orientation, not their current order on screen. Preview scaling does not change exported coordinates. The inspector refreshes less frequently than the image. Stopping the stream retains its last image and details, with the stream marked stopped.

## Performance and validation

Detection runs in native AprilTag CPU code, with OpenCV image operations and separate capture and detection threads. The pipeline keeps the newest frame rather than building an expanding queue. This version does not run AprilTag detection on CUDA or another GPU backend.

For tuning, show a representative scene containing tags and click **Benchmark CPU threads**. Capture stops while several worker counts process the same frame. The app selects the lowest median processing time and restarts. This also closes any active CSV recording. Benchmark again after changing resolution or detector settings.

The benchmark includes image conversion, detection, filtering, and enabled pose calculations. Detector construction, camera capture, and screen drawing are excluded. A fast benchmark cannot make the webcam deliver an unsupported FPS.

Development checks on an 8-core Windows x64 system produced these results:

| Check | Observation | Scope |
| --- | --- | --- |
| Generated 1920 × 1080 image, two tags, full detail, 8 detector threads | 9.59 ms median over five timed runs after warmup | Synthetic detector processing, including synthetic calibrated pose; not camera FPS. |
| Same generated image, 16 threads | 9.64 ms median | Additional threads did not improve this particular scene. |
| Logitech C922, 1920 × 1080 MJPG, requested 30 FPS | Approximately 30 delivered FPS | Short live camera checks; no physical AprilTags were in view. |
| Same webcam, 1280 × 720 MJPG, requested 60 FPS | Approximately 30 delivered FPS despite the driver reporting 60 | 60 FPS delivery was not demonstrated in that setup. |

These observations are examples, not guaranteed rates. Scene complexity, light, exposure, drivers, image dimensions, and other running applications affect performance. Detection and pose were also checked with generated/official tag imagery and known geometry. **Physical printed-tag range and calibrated real-world distance accuracy were not established by those checks.**

Good lighting, sharp focus, less motion blur, and a larger tag in the image can improve results more than extra worker threads.

## Export measurements

**Save snapshot + JSON** writes an annotated camera-preview PNG and a matching JSON file. The PNG uses the displayed preview dimensions and can include letterboxing. JSON coordinates and image dimensions refer to the original captured frame. Metadata records the settings applied to that processed frame, plus camera request/capture information.

**Record detections to CSV** writes a row per detected tag in each result observed by the UI. Rows contain time, frame number, resolution, rates, decoding measurements, and optional pose. Frames with no tags produce no data rows. The UI polls the newest result, so it can omit intermediate detector results when processing outpaces UI updates. CSV timestamps are export wall-clock times, not sensor timestamps. This is a detection log, not a complete frame log or video recording.

**Save 3D view** has a separate PNG/JSON export describing the scene's camera/tag transforms, reference frame, units, and live/example state. See [3D export details](3D_POSE.md#export-a-scene).

Stop recording or stop capture to close the CSV file. Settings are session-specific; save calibration JSON and reload/reapply the desired settings in future sessions.

## Examples and shortcuts

**Demo** generates moving tag imagery and runs the detector without opening a webcam. It supports `tag36h11`, `tag16h5`, and `tag25h9`. Metric pose is intentionally disabled for that video.

**Example scene** shows independent, known 3D geometry without camera input. Its simulated positions never enter live detection measurements or CSV output.

| Shortcut | Action |
| --- | --- |
| Space | Start or stop capture. |
| F11 | Toggle fullscreen. |
| Ctrl+S | Save camera snapshot and JSON. |

Source command-line examples, from the repository directory:

```powershell
.\.venv\Scripts\python.exe app.py --demo --view split
.\.venv\Scripts\python.exe app.py --pose-example
.\.venv\Scripts\python.exe app.py --camera --view camera
```

`--view` accepts `camera`, `pose`, or `split`. The portable executable accepts the same arguments. `--camera` starts the default webcam immediately; the example flags do not require one.

## Troubleshooting

**Camera will not open:** close other webcam applications, check its USB connection and Windows camera permissions for desktop apps, and try another listed device. Stop capture before changing DSHOW/MSMF or MJPG/YUY2/Default. Camera enumeration and backend indices can differ between drivers.

**FPS is below the request:** check the camera's supported modes, improve lighting, try a lower resolution or MJPG, and compare measured FPS. Driver-reported FPS is not evidence of delivered FPS. Exposure/focus controls are driver-dependent.

**Tags are missing or flickering:** confirm the family; show the entire tag and white border; improve focus and lighting; move closer; use decimation 1.0. Avoid glare and motion blur. A lower margin threshold can expose weak decodes but also accept less reliable ones.

**3D scene is empty:** follow its status message. Load a matching calibration, apply the measured tag edge, and make a tag visible. In selected-tag origin mode, the chosen anchor must have a unique visible valid pose. Stale streams and pending settings intentionally hide geometry.

**Distance is wrong:** recheck physical tag size, calibration, camera mode, focus, crop/zoom, and target flatness. A low reprojection error cannot detect an incorrect physical tag size. See [Calibration](CALIBRATION.md).
