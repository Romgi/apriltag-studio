# Camera calibration

[Project home](../README.md) · [User guide](USER_GUIDE.md) · [3D pose](3D_POSE.md)

Calibration relates image pixels to the camera's lens geometry. AprilTag Studio needs it, together with the measured tag edge, to estimate distance and 3D orientation. IDs, outlines, and pixel coordinates work without calibration.

Use a calibration for the **same camera, lens, focus setting, and capture mode** as the live session. All tags currently share one configured physical edge length; mixed sizes produce incorrect distances unless they match that setting.

## Prepare the checkerboard

1. Open [checkerboard.svg](../assets/checkerboard.svg), or use **Save printable checkerboard…** in the calibration dialog.
2. Print on **A4 landscape**, at **100% / actual size**. Disable fit-to-page scaling.
3. Mount the print on a flat, rigid surface and measure a square with a ruler.

The template has **10 × 7 squares**, producing **9 columns × 6 rows of inner corners**. Each square is nominally **20 mm** wide. Enter the actual measured width if printing changes it. The dialog asks for inner corners, not square counts.

## Capture and solve

1. Start the real webcam in the capture mode you intend to use. Set focus and keep it unchanged. Disable autofocus if the camera supports a suitable fixed-focus setting.
2. Click **Calibrate this camera…**.
3. Enter **Columns 9**, **Rows 6**, and your measured **Square width** in millimetres.
4. Keep the full board visible and capture at least **12 distinct, sharp views**. Vary position, distance, and tilt; include board positions near every image edge. Avoid glare, motion blur, and a sequence of nearly identical front-facing views.
5. Click **Solve calibration** when enough samples have been accepted.
6. Read the RMS reprojection error, then click **Save calibration JSON…** to keep the result.
7. Click **Use calibration** to apply it to the current session. Saving and applying are separate actions.

The dialog detects corners and solves calibration in a worker thread. Nearly duplicate views are rejected. Accepted samples lock the board specification; use **Reset samples** to change it. A camera-resolution change also requires starting over.

Lower RMS error means the model fits the sampled corners more closely. It does **not** certify distance accuracy. Sharp, varied views and a flat, accurately measured board matter; simply reaching the minimum count is insufficient. Calibration uses OpenCV's pinhole lens model. [OpenCV camera-calibration reference](https://docs.opencv.org/4.x/dc/dbb/tutorial_py_calibration.html).

## Set the AprilTag edge

After applying calibration, set **Tag edge** and click **Apply detection settings**. This is the physical distance between the tag's detection corners, not the full paper or image size.

For `tag36h11`, measure the black outer square and exclude its white margin. The supplied [printable samples](../assets/tag36h11-samples.html) are designed for **120 mm** edges at actual print size; measure the result. The application's initial value is **165.1 mm**, so change it for these samples.

Other tag layouts can place their detection boundary differently. Follow the upstream [AprilTag size definition](https://github.com/AprilRobotics/apriltag#pose-estimation).

An incorrect tag edge scales estimated translations and distances even when the observed pixel fit looks good. A low reprojection error cannot verify that you measured the tag correctly.

## Reuse a calibration

Click **Load calibration** and select the saved JSON. The required fields are:

| Field | Contents |
| --- | --- |
| `camera_matrix` | Finite 3 × 3 camera intrinsic matrix with positive focal lengths. |
| `dist_coeffs` | OpenCV distortion coefficients; supported vector lengths are 4, 5, 8, 12, or 14. |
| `image_width`, `image_height` | Positive calibration image dimensions in pixels. |

Files produced by the dialog also record RMS error, per-view errors, sample count, square size, and board dimensions. Keep them separate from the generated `portable` directory so rebuilding the application does not replace them.

The app blocks pose when calibration and capture aspect ratios differ by more than its tolerance. For matching ratios it scales the intrinsic matrix to the image size. **Matching aspect ratios do not prove an unchanged field of view:** webcam modes can crop differently even with the same ratio.

Recalibrate after changing capture mode, lens, focus, crop, or digital zoom. Only reuse scaled intrinsics when the new image is a scale of the same optical view. The app cannot establish this from dimensions alone.

## Check the result in practice

Show a flat tag of the configured size at independently measured positions and orientations. Compare several distances and image locations, including the edges. Inspect repeatability and reprojection error while watching for focus changes and motion blur.

Square planar targets can have ambiguous or unstable pose estimates, especially when small or nearly front-facing. The app chooses a valid positive-depth square-pose solution with the lowest corner reprojection error. This does not remove every ambiguity. Synthetic regression tests verify the implementation's geometry; physical accuracy depends on the actual camera, calibration, print, and scene.

For coordinate conventions, reference frames, and interpretation of roll/pitch/yaw, continue to the [3D pose guide](3D_POSE.md#coordinates-and-orientation).
