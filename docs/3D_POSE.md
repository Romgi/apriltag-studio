# 3D pose scene

[Project home](../README.md) · [User guide](USER_GUIDE.md) · [Calibration](CALIBRATION.md)

The 3D scene shows the camera and each currently visible tag with a valid calibrated pose. It visualizes the latest processed frame, **not a persistent map**. Tags outside the camera's view are not tracked or remembered as live geometry.

![Illustrative camera and AprilTag poses in the 3D viewer](images/pose-view.png)

*This screenshot shows the simulated Example scene. Live geometry requires a camera calibration and the measured physical tag edge.*

## Start a live scene

1. Start the webcam and complete the [calibration workflow](CALIBRATION.md), or load a matching calibration JSON.
2. Enter the measured **Tag edge** and click **Apply detection settings**.
3. Show tags to the camera. Choose **Split** to see image detections and their 3D poses together, or **3D Pose** for a larger scene.

Every tag uses the same applied edge length. Incorrect or mixed physical sizes produce incorrect distances. The scene uses the settings of the processed frame; editing a control without applying it does not silently rescale existing geometry.

## Move around and select tags

| Action | Result |
| --- | --- |
| Left-drag | Orbit the scene. |
| Right-drag or Shift-drag | Pan. |
| Mouse wheel | Zoom. |
| Fit or double-click | Frame the camera and visible tags. |
| Reset | Restore the orbit view. |
| Orbit / Top / Front | Switch inspection viewpoints. |
| Camera | Use the camera's optical viewpoint; orbit, pan, and zoom are disabled. |
| Click a live tag plate | Select the same tag in the inspector and camera overlay. |

The Camera preset uses the calibrated intrinsic matrix for a pinhole projection. The renderer does not reproduce lens distortion; raw camera pixels can differ near distorted image edges even when the estimated pose is correct.

## Choose a reference frame

**Camera origin** places the camera at the origin and expresses tags relative to it. The camera coordinate axes are +X right, +Y down, and +Z forward.

**Selected tag origin** places the selected tag at the origin and expresses the camera and other visible tags relative to that tag. Select an inspector row or click a live tag plate to choose the anchor. Choosing another tag while this mode is active changes the anchor explicitly.

The anchor stays tied to its selected ID when the inspector automatically selects another visible tag. If the anchor disappears, has no valid pose, or appears more than once, the scene hides geometry and reports the missing or ambiguous anchor. It does not silently switch origin or continue displaying a guessed camera position.

Changing the reference frame is a coordinate transform, not an independent camera-localization or mapping method. It is recomputed from current tag observations.

## Live, stopped, stale, and example states

| State | Scene behavior |
| --- | --- |
| LIVE | Displays valid calibrated poses from the latest processed frame. |
| No valid poses | Clears measured tag geometry and explains what is missing. |
| Settings pending | Hides geometry until a result processed with the newly applied settings arrives. |
| STOPPED | Retains the last scene, clearly labeled as frozen. |
| STALE | Hides live geometry when recent camera results stop arriving. |
| EXAMPLE | Displays clearly labeled simulated geometry, independent of detector measurements. |

**Example scene** supplies three known tag poses and a camera without requiring hardware or calibration. Clicking an example plate selects it only within the example; the live inspector and anchor remain separate. The example does not change detections, calibration, or CSV measurements.

The main **Demo** checkbox serves a different purpose: it generates video for exercising the AprilTag detector. Metric pose is disabled in that generated video, so it does not automatically populate a calibrated live 3D scene.

From source, open the illustrative scene with:

```powershell
.\.venv\Scripts\python.exe app.py --pose-example
```

## Coordinates and orientation

Translations are in **metres**. In the camera frame, X/Y/Z locate the tag center relative to the optical center. Range is the Euclidean distance to that center, so it can exceed Z.

Each marker's local origin is its center. With `s` equal to half the measured tag edge, native corner indices have these local coordinates:

| Corner | Local coordinates |
| --- | --- |
| 0 | `(-s, +s, 0)` |
| 1 | `(+s, +s, 0)` |
| 2 | `(+s, -s, 0)` |
| 3 | `(-s, -s, 0)` |

Local +X points toward the side between corners 1 and 2; local +Y points toward the side between corners 0 and 1. +Z follows the right-hand rule. These axes follow the tag's decoded orientation, rather than remaining fixed to screen directions.

The inspector's roll, pitch, and yaw describe the **tag-to-camera** rotation in degrees:

```text
R = Rz(yaw) · Ry(pitch) · Rx(roll)
point_in_camera = R · point_in_tag + translation
```

Euler angles can jump near singular orientations. Native rotation matrices are preserved for scene transforms. Image rotation in the detector inspector is a separate 2D measurement of corner 0 → corner 1 and should not be interpreted as 3D yaw.

For an anchor pose `(Ra, ta)` in the camera frame, the selected-tag scene uses:

```text
camera rotation in anchor frame    = transpose(Ra)
camera translation in anchor frame = -transpose(Ra) · ta
tag rotation in anchor frame       = transpose(Ra) · Rtag
tag translation in anchor frame    = transpose(Ra) · (ttag - ta)
```

These transforms preserve relative distances and camera projection. Reprojection error remains an image-space fit measurement in pixels, not a physical accuracy guarantee.

## Export a scene

**Save 3D view** saves the current scene PNG plus a JSON file containing:

- Camera and tag rotations/translations, each mapping local coordinates into the selected scene frame.
- Tag IDs, families, applied sizes, and available pose-error measurements.
- Camera intrinsics, image size, reference frame, status, and stream state.
- Explicit metre units and the transform convention `point_in_scene = rotation @ point_local + translation`.

Example exports retain `demo: true`, an example stream state, and simulated-geometry labeling. Stopped scenes retain their frozen state. An unavailable scene has no valid live geometry; consumers must honor its `available` field rather than treating placeholder camera fields as measurements.

This export is separate from camera snapshots and CSV detection logs. It does not record a temporal trajectory or create a persistent tag map.
