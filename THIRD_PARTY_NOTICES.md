# Third-party notices

Original Novel Studio core files use MIT. The following selected upstream files retain their original copyright and MIT license; they are not relabeled as Novel Studio-owned works.

| Component | Original copyright | License file |
|---|---|---|
| oh-story | Copyright (c) 2025-2026 oh-story-claudecode | [Original license](vendor/oh-story/LICENSE) |
| human-writing | Copyright (c) 2026 Human Writing Skill contributors | [Original license](vendor/human-writing/LICENSE) |
| humanizer-zh | Copyright (c) 2026 歸藏 | [Original license](vendor/humanizer-zh/LICENSE) |

See UPSTREAM_SOURCES.md and integrations.lock.json for every pinned file, source commit and checksum. Humanizer-zh keeps its original metadata describing its earlier Humanizer/PR/reference lineage; that lineage remains a release-review item.

No InkOS, Webnovel Writer, SillyTavern, Dify, mem0, Sepia, Story Skills, Vale or LanguageTool code is bundled in the current runtime. Mentioning a project in research does not copy or relicense it.

The optional upstream manifest schema is available from https://antigravity.google/schemas/v1/plugin.json. The pinned schema tests can use a separately obtained copy at `docs/research/antigravity-plugin.schema.json`. The source candidate omits the cached copy.

GitHub Actions and jsonschema are development-only tools. See `.github/workflows/checks.yml` and `requirements-test.txt` for their pins.

## Desktop runtime additions (Phase 1.2.4.1)

The public Windows ZIP ships the application with its selected source integrations and notices. On first launch, confirmed setup downloads the pinned CPython and dependency archives from Python.org and PyPI and installs their selected runtime files locally. The ZIP itself contains no Python, Qt, Microsoft runtime DLLs or Pandoc executable. The local development bundle described below has a separate runtime closure.

The MIT vendor list above describes the plugin payload, not the license of the entire separate desktop runtime. Qt/PySide 6.12.0 notices are retained under `third_party/qt-6.12.0`; the Python runtime retains its own LICENSE.txt. These components are not relicensed as Novel Studio MIT code.

| Import dependency | Pinned version | Original terms / notice |
|---|---|---|
| markdown-it-py | 4.2.0 | MIT plus original markdown-it notice |
| mdurl | 0.1.2 | MIT / original Joyent notice |
| pypdf | 6.21.4 | BSD-3-Clause |
| python-docx | 1.2.0 | MIT |
| lxml | 6.1.3 | BSD plus its bundled libxml/libxslt/ElementTree notices |
| striprtf | 0.0.35 | BSD-3-Clause |
| typing_extensions | 4.16.0 | PSF-2.0 |

Original files remain under `third_party/import-parsers`. Exact official wheel hashes and notices are in the [dependency audit](docs/versions/1.2.4/testing/phase1_2_4_1/parser-dependencies.json); [release-source commits](docs/versions/1.2.4/testing/phase1_2_4_1/release-source-references.json) are recorded separately. No upstream parser source is modified. The lxml ElementTree notice referenced by its wheel is also retained with [source evidence](docs/versions/1.2.4/testing/phase1_2_4_1/additional-license-source.json). Optional PDF external-decoder invocation is disabled by public library configuration, not by changing upstream source. No AGPL EbookLib code is included.
