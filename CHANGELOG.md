# Changelog

## 0.1.0 — Initial public release

- Native AprilTag 3 detection with independent, latest-frame webcam capture and adjustable CPU threading.
- Live outlines, IDs, corners, measured capture/detection FPS, resolution, decode margin, Hamming corrections, homography, and optional calibrated pose.
- Camera, 3D Pose, and Split views with textured tags, camera/frustum, metric grid, axes, and orbit/pan/zoom controls.
- Camera-origin and selected-tag-origin coordinates, with explicit missing-anchor, stale-frame, and calibration states.
- Checkerboard calibration, printable tag samples, CPU benchmarking, snapshots with JSON, CSV detection logs, and 3D scene exports.
- Windows x64 portable build, reproducible build and packaging scripts, and automated tests.

This release uses CPU detection. Metric pose requires calibration and the correct physical tag edge. The 3D scene contains currently visible tags; it does not build a persistent map.
