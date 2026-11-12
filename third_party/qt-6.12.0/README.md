# Qt 6.12.0 local desktop dependency notices

Novel Studio's own source remains MIT. The desktop dynamically loads the unmodified
official PySide6 Essentials / Shiboken6 6.12.0 Windows x64 wheels. Qt Core, Gui,
Widgets and the selected platform plugins use the LGPLv3 option; GPL-only Addons,
WebEngine, QML and Qt Designer are not distributed in this desktop closure.

The pinned upstream sources and corresponding source access are:

- [PySide / Shiboken source](https://github.com/qtproject/pyside-pyside-setup/tree/f50facb83fc3dc5f3240c854668c7a6dd93871b2)
- [QtBase source](https://github.com/qt/qtbase/tree/025bdad181de241e81bf853c8a2d7bf3d19261a9)
- [QtBase complete source archive](https://codeload.github.com/qt/qtbase/tar.gz/025bdad181de241e81bf853c8a2d7bf3d19261a9)
- [Qt for Python build instructions](https://doc.qt.io/qtforpython-6/building_from_source/index.html)

LGPL/GPL alternative texts and Qt's exception are retained here. `qtbase-notices`
contains a superset of QtBase source license/third-party attribution files, copied
verbatim from the pinned source archive. Extra notices do not enable those modules.
No third-party executable source from that notice directory is integrated.

The DLLs remain separate and replaceable; there is no static linking, DRM, EULA
restriction on reverse engineering for debugging LGPL modifications, remote asset
download or system installation of Qt. Microsoft fonts are loaded from Windows,
not copied or redistributed. CPython notices accompany the runtime.

This is an unsigned, internal local alpha build, not a public release. Before any
public binary distribution, review the corresponding-source delivery/build
instructions, module attribution closure, Microsoft VC runtime redistribution
terms and current dependency vulnerabilities. The current source review does not
claim a complete security audit of Qt or resolve these public-release gates.
