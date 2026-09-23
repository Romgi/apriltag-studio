# Contributing

Bug reports and focused pull requests are welcome. See [development](docs/DEVELOPMENT.md) for setup, tests, and Windows builds.

For a camera issue, include the app version, Windows version, webcam model, requested video mode, actual resolution/FPS, capture API, USB format, and relevant error text. State whether the generated demo works. For detection or pose issues, include the family, detector settings, calibration mode, and measured tag edge. Only share camera images or calibration files you intend to make public.

Keep capture independent of detection, preserve the latest-frame behavior, and use the settings attached to a result when interpreting its measurements. Do not present estimated/example geometry as calibrated measurements. Add regression coverage for behavior changes, and update user documentation when controls or measurement meanings change.

Run the tests before submitting a pull request. Changes to camera capture or packaging should also describe the Windows/camera or executable smoke checks performed. No physical webcam is required by the automated test suite.

Contributions to the application's original code are provided under its MIT license. Existing third-party files retain their own licenses and notices.
