# Build and test

Run source checks on Windows with Python 3.10 or 3.13 and Git. Use a clean virtual environment. Use synthetic projects and fixtures in tests.

```text
python -m pip install -r requirements-test.txt
python -B scripts/security_check.py
python -B tests/check_documentation.py
```

Set an absolute report path in an existing directory outside the checkout. Use the portable module list in the `verify` job of `.github/workflows/checks.yml` for the core checks. For the full desktop suite, install the Windows desktop, import and provider dependencies below, then run `python -B tests/run_rc1_suite.py --out <report-path>`. The source runner uses an isolated profile and synthetic input. The optional upstream Antigravity schema can be supplied separately at `docs/research/antigravity-plugin.schema.json`. Its expected SHA256 is pinned in the schema tests.

## Run the Windows offline sample

Use Windows x64 with Python 3.13. Install the locked dependencies into a virtual environment.

```powershell
python -m venv .venv-workbench
.\.venv-workbench\Scripts\python.exe -m pip install --require-hashes -r requirements-desktop.txt -r requirements-import.txt -r requirements-providers.txt
.\.venv-workbench\Scripts\python.exe -B desktop/desktop-entry.pyw --demo
```

## Build

- Build the Antigravity component with `python -B tools/build_plugin.py --out <new-absolute-folder>`. Place it outside the checkout.
- The Windows desktop builder needs a clean committed checkout, Python 3.13 x64, the .NET Framework C# compiler, and the original locked wheel archives. Put the Qt, import parser and provider wheels in `.build/qt-wheels`, `.build/import-wheels` and `.build/provider-wheels`. Install the three requirements files into `.venv-workbench`. For a local desktop build, put the official archive identified in `tools/pandoc-lock.json` in `.build/pandoc-3.13-audit`.
- Run `.\.venv-workbench\Scripts\python.exe -B tools/build_workbench.py --out <new-absolute-folder>`. The builder checks the locked archives and records source and payload hashes. This desktop build configuration is for local testing. See the [third-party notices](THIRD_PARTY_NOTICES.md) for redistribution status.

Report issues using synthetic reproductions and preserve upstream license notices. Review the [security policy](SECURITY.md) before sharing diagnostics.

Protected folders can be added through Settings → Advanced and installation → Diagnostics and installation. They apply to new projects and are saved separately from layout preferences. Existing project policies remain bound to their saved metadata. A local deployment can provision `.local/host-protection.json` with format `novel-studio-protected-folders-v1` and an absolute-path `paths` array. This ignored file is copied only when present, and its hash is recorded in the local bundle manifest. Review local deployment configuration before distributing a binary.
