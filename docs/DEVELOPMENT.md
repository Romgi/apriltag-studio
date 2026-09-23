# Development

AprilTag Studio is a native Windows application. The supported development and
packaging environment is **64-bit Windows with Python 3.13**. The runtime and
development dependencies are pinned in `requirements.txt` and
`requirements-dev.txt`.

## Run from source

Open PowerShell in the repository root:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe app.py
```

An activated environment is optional; these commands select the interpreter
explicitly. A USB camera is only needed for live capture. To exercise the UI
without one:

```powershell
.\.venv\Scripts\python.exe app.py --demo --view split
.\.venv\Scripts\python.exe app.py --pose-example
```

The detector demo generates moving tag images. The 3D example is a separately
labeled illustrative scene. Neither provides measured webcam poses. Live metric
pose requires a camera calibration at a compatible aspect ratio and the actual
printed tag size.

## Run tests

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
.\.venv\Scripts\python.exe -m pytest tests -q
Remove-Item Env:QT_QPA_PLATFORM
```

The tests exercise detection, calibration validation, exports, pose transforms,
rendering, and Qt interaction. Camera enumeration and capture are replaced where
needed; the suite does not require physical camera access. Clearing
`QT_QPA_PLATFORM` restores normal windows when you run the app interactively.

## Build the portable app

Build with your existing development environment:

```powershell
.\Build.ps1 -PythonExecutable (Resolve-Path '.\.venv\Scripts\python.exe').Path -SkipInstall
```

Omit `-SkipInstall` to let the build script install its pinned dependencies.
Running `.\Build.ps1` without an interpreter creates a separate `.build-venv`
using the Python 3.13 launcher. Build output goes to
`portable\AprilTagStudio.exe`; keep its `_internal` directory beside the executable.
The script replaces the generated `portable` directory, so close an existing
copy of that build first.

PyInstaller creates a Windows application without a console. To verify a packaged
build, launch it with a bounded runtime and inspect the generated JSON report:

```powershell
New-Item -ItemType Directory -Force -Path smoke-reports | Out-Null
$example = Start-Process -FilePath '.\portable\AprilTagStudio.exe' -ArgumentList '--pose-example --quit-after 3 --report smoke-reports/pose-example.json' -WindowStyle Hidden -Wait -PassThru
if ($example.ExitCode -ne 0) { throw 'Pose example failed.' }
$demo = Start-Process -FilePath '.\portable\AprilTagStudio.exe' -ArgumentList '--demo --view split --quit-after 5 --report smoke-reports/detector-demo.json' -WindowStyle Hidden -Wait -PassThru
if ($demo.ExitCode -ne 0) { throw 'Detector demo failed.' }
Get-Content smoke-reports/pose-example.json
Get-Content smoke-reports/detector-demo.json
```

The pose example report should contain three tags, `pose_scene.demo: true`,
`pose_scene.available: true`, and `has_result: false`. The detector demo should
contain frames and detections, an empty `error`, and no measured 3D poses.

## Package a release

After testing the portable build:

```powershell
.\.venv\Scripts\python.exe scripts/package_release.py --version 0.1.0 --output-directory dist
```

This creates `dist/AprilTagStudio-v0.1.0-windows-x64.zip` and its `.zip.sha256`
checksum. The package includes the portable application, source, launchers,
documentation, assets, and third-party licenses. Use `--portable-directory` to
package another build directory. Update the version when preparing a release;
do not commit generated executables, environments, smoke reports, or archives.

## CI

[The Windows workflow](../.github/workflows/ci.yml) runs on pushes to `main`, pull
requests, and manual dispatch. It installs the pinned dependencies, runs all
tests, builds the portable executable, and checks both packaged demo paths without
opening a real camera. It then uploads a ZIP, checksum, and smoke reports as a
14-day Actions artifact. It does not publish a GitHub release.

The workflow uses read-only repository permissions and pins each GitHub-owned
action to a full commit SHA. The pinned releases were verified against the
official repositories: [checkout v7.0.1](https://github.com/actions/checkout/releases/tag/v7.0.1),
[setup-python v7.0.0](https://github.com/actions/setup-python/releases/tag/v7.0.0),
and [upload-artifact v7.0.1](https://github.com/actions/upload-artifact/releases/tag/v7.0.1).
When updating a pin, confirm the release commit and runtime in the action's own
repository, and rerun CI.

## Source layout

| File | Responsibility |
| --- | --- |
| `app.py` | Main window, controls, exports, and command-line entry point |
| `engine.py` | Camera capture, threaded detection, and calibrated pose estimation |
| `calibration.py` | Camera calibration tools |
| `pose_geometry.py` | Validated current-frame poses and scene transforms |
| `pose_panel.py` | Pose controls, status, and illustrative example |
| `pose_view.py` | Qt perspective rendering and mouse interaction |
| `tests/` | Automated engine, UI, and geometry checks |

Use metres for translations and tag sizes. Live transforms use right-handed
OpenCV coordinates: camera X right, Y down, Z forward. In a selected-tag reference,
the scene axes are tag-local. Do not retain lost tags as live geometry or make a
demo scene appear to be a measured pose.
