# Third-party components

AprilTag Studio uses the following runtime components. Original license and notice files copied from the installed distributions are in `licenses/`; `licenses/manifest.json` records each file's component, version, source path or URL, and SHA-256 checksum. Embedded dependency notices supplied with NumPy and OpenCV are preserved alongside their main licenses.

| Component | Version | License identified by the package or upstream | Project |
|---|---|---|---|
| CPython | 3.13.7 | Python Software Foundation License and included third-party notices | [Python](https://www.python.org/) |
| pyapriltags / native AprilTag | 3.4.3.1 | BSD; original AprilTag and wrapper notices included | [pyapriltags](https://github.com/WillB97/pyapriltags), [AprilTag](https://github.com/AprilRobotics/apriltag) |
| OpenCV Python headless | 5.0.0.93 | Apache-2.0; additional bundled component notices | [OpenCV Python](https://github.com/opencv/opencv-python) |
| NumPy | 2.5.3 | BSD-3-Clause; additional bundled component notices | [NumPy](https://numpy.org/) |
| PySide6 Essentials | 6.11.2 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only, as declared in the installed package metadata | [Qt for Python](https://doc.qt.io/qtforpython-6/) |
| Shiboken6 | 6.11.2 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only, as declared in the installed package metadata | [Shiboken](https://doc.qt.io/qtforpython-6/shiboken6/) |
| psutil | 7.2.2 | BSD-3-Clause | [psutil](https://github.com/giampaolo/psutil) |
| pygrabber | 0.2 | MIT | [Python Grabber](https://github.com/andreaschiavinato/python_grabber) |
| comtypes | 1.4.17 | MIT | [comtypes](https://github.com/enthought/comtypes) |

## Qt and PySide

This application uses the open-source Qt/PySide libraries as separately loaded dynamic libraries. Their original DLLs and package layout are retained, rather than being statically merged into the application. The included Python source and `requirements.txt` allow running or rebuilding the application with replacement, compatible Qt/PySide libraries. The application does not impose restrictions on replacing those libraries or debugging modifications to them.

The installed PySide6 and Shiboken6 wheels contain a `LicenseRef-Qt-Commercial.txt` reference file. That file is copied unchanged. The LGPLv3, GPLv2, GPLv3 and other license reference texts from the matching PySide 6.11.2 upstream source release are supplied separately in `licenses/Qt-PySide-6.11.2-upstream/`.

Upstream library source is available from the versioned [PySide 6.11.2 source tree](https://github.com/pyside/pyside-setup/tree/v6.11.2) and [Qt Base 6.11.2 source tree](https://github.com/qt/qtbase/tree/v6.11.2). Qt documents its components and their notices in [Licenses Used in Qt for Python](https://doc.qt.io/qtforpython-6/licenses.html) and [Third-Party Code Used in Qt](https://doc.qt.io/qt-6/licenses-used-in-qt.html). The copied license texts retain their original terms and notices.

The bundled Qt Core, GUI, Widgets, Network, and SVG libraries and image format plugins also contain third-party code. Original official attribution pages for the matching Qt 6.11.2 release are retained in [`licenses/Qt-6.11.2-third-party/`](licenses/Qt-6.11.2-third-party/README.md), including XSVG, TinyCBOR, MD4C, font and image libraries, and other upstream notices. The collection preserves all 52 attribution pages listed under Qt Core, GUI, Network, SVG, and Image Formats, plus the upstream index; Qt Widgets has no separate section in that index. It includes optional and other-platform components from those sections without asserting that every component is active in this build.

These HTML files preserve the original copyright and license notices and have individual source URLs and SHA-256 hashes in `licenses/manifest.json`. The Qt documentation's GFDL 1.3 license text is supplied in `licenses/Qt-PySide-6.11.2-upstream/GFDL-1.3-no-invariants-only.txt`; the third-party software retains its own stated terms.

The included `PySide6/opengl32sw.dll` software OpenGL library identifies itself as Mesa 11.2.2. The same notice folder additionally preserves Qt's official Mesa llvmpipe and LLVM attribution pages, including their embedded component notices. Qt's description of development tools on the LLVM page does not mean those tools are included with this application.
