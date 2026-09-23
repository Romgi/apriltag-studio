# Validation and reference measurements

Measured on September 23, 2026, using the connected Logitech C922 USB webcam and Windows x64. The PC has an AMD Ryzen 7 7800X3D (8 cores / 16 logical processors), NVIDIA GeForce RTX 5070 Ti, and 32 GB RAM. The tested Python runtime is CPython 3.13.7 x64.

AprilTag detection runs in the native AprilTag library on the CPU. This build does not perform CUDA detection on the NVIDIA GPU. The default configuration uses eight detector worker threads; the operating system schedules them, without fixed core affinity. The app's tuning control can measure other thread counts on the current scene.

## USB camera measurements

Capture FPS below comes from timed successful frame reads, after discarding the first three reads in approximately three-second probes. It is separate from the driver's advertised FPS and detector throughput. No camera images were saved by these probes.

| Requested mode | Negotiated format | Driver FPS | Measured capture FPS |
|---|---|---:|---:|
| Initial 1920 × 1080 / 30 setup | YUY2 | 30 | 5.00 |
| Corrected 1920 × 1080 / 30, auto exposure | MJPG | 30 | 29.99 |
| Corrected 1920 × 1080 / 30, manual exposure −6 | MJPG | 30 | 29.99 |
| 1280 × 720 / 60, manual exposure −6 | MJPG | 60 | 29.68 |

The initial setup requested MJPG before setting FPS. OpenCV's DirectShow FPS setter recreated the stream and discarded the requested format, leaving YUY2 at five delivered frames per second. The shipped engine sets FPS and resolution first, then requests MJPG last. This restored approximately 30 delivered frames per second at full HD on the connected camera. The 720p request was accepted as 60 FPS by the driver, but only about 30 FPS was delivered during this test; 60 FPS capture has not been demonstrated on this setup. See the [OpenCV DirectShow implementation](https://github.com/opencv/opencv/blob/4.x/modules/videoio/src/cap_dshow.cpp).

## Native detection benchmark

One generated 1920 × 1080 frame containing two tags was processed at full detection resolution (`decimate=1.0`). Each detector was constructed once, warmed up twice, then timed five times; the table reports medians. Timings include grayscale conversion and pose calculation using synthetic camera intrinsics, and exclude camera capture, detector construction, and display drawing.

| Detector threads | Median processing time |
|---:|---:|
| 1 | 27.85 ms |
| 2 | 16.83 ms |
| 4 | 11.89 ms |
| 8 | 9.59 ms |
| 16 | 9.64 ms |

Eight threads were fastest in this small benchmark. More threads did not improve this particular scene. These figures describe generated imagery and vary with scene complexity, lighting, settings, and other programs running on the PC. They are not measured camera FPS or a guaranteed maximum.

## Detection and pose checks

The 19 engine tests passed after the camera setup changes. Checks covered synthetic IDs, multiple tags, rotation and perspective, real one-bit tag corruption and Hamming filtering, margin filtering, invalid calibration, calibration aspect-ratio rejection, printable sample tags, and known geometric pose with scaled intrinsics. Independently projected corners recovered a known translation of `[0.10, −0.05, 1.25]` metres. Native corner orientation was compared with an official AprilRobotics tag image; the demo raster was aligned with that canonical orientation.

The live camera scene contained no AprilTags during the camera throughput check. **Physical printed-tag acquisition, detection range, and real calibrated distance accuracy have not been tested.** Detection was verified with generated and official tag imagery; pose was verified with synthetic geometry. Accurate real distances require calibration for the webcam and the measured tag edge length.

The displayed capture-to-result time starts after a frame read completes. It includes processing and waiting inside the application, but does not include unknown sensor exposure or driver buffering time.

## Final application and executable checks

The combined suite passed **30 tests**, including detection, worker start/stop/restart, camera failure handling, delivered versus requested resolution, UI reconfiguration, applied-settings snapshot metadata, CSV export, calibration loading, and accepted/cancelled calibration dialogs.

The final portable executable was tested directly, separately from source execution:

- Generated 1080p demo: 30.0 capture FPS, 30.0 detection FPS, 9.9 ms processing, two correctly decoded tags, clean exit.
- Logitech C922 at 1920 × 1080: 29.9 capture FPS, 29.9 detection FPS, 16.0 ms processing in the current room scene, zero skipped frames in the short run, clean exit. No physical tags were present.

Packaging validation caught an unrelated Poppler ICU DLL being collected from the build machine's PATH. The final build uses a restricted DLL search path, documented in `Build.ps1`, and passes both executable checks without that conflicting DLL. The camera was released after testing and auto exposure restored by the final default-camera run.

## 3D pose update

The updated source passes **65 automated tests**. New checks verify camera-to-tag reference transforms, preserved pairwise distances and camera reprojections, calibrated scene scale, invalid and lost poses, ambiguous duplicate anchors, exact rotation matrices, camera-view projection, mouse orbit/zoom/selection, and live UI invalidation after calibration changes. Example-scene selections and exports remain separate from live detector measurements.

An end-to-end generated-image check passed through native AprilTag detection, calibrated pose estimation, the scene model, and the Split UI. IDs 1 and 7 appeared in the 3D scene; selecting tag 1 as origin put its translation at zero while correctly transforming the camera and other tag.

The rebuilt `portable/AprilTagStudio.exe` passed two direct smoke checks with clean exits: the 3D example rendered all three simulated tag objects and its camera, and the detector demo ran in Split view while correctly withholding uncalibrated metric geometry. Its native Windows 3D example screenshot was visually inspected. Real-world calibrated 3D accuracy remains dependent on the user's camera calibration and physical tag measurements.
