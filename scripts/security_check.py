#!/usr/bin/env python3
"""Offline source/provenance gate. Reviewed author I/O is separate from Agent tools."""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
ALLOWED_IMPORTS = {"__future__", "argparse", "ast", "hashlib", "importlib", "io", "json", "pathlib", "re", "stat", "sys", "typing"}
REVIEWED_IMPORTS = {"scripts/secure_read.py": {"contextlib", "os", "ctypes"},
                    "scripts/novelctl.py": {"secure_read", "types"},
                    "scripts/safe_project.py": {"contextlib", "os", "uuid", "secure_read"},
                    "scripts/workspace_admin.py": {"contextlib", "difflib", "os", "uuid", "safe_project", "subprocess"},
                    "gui/service.py": {"contextlib", "datetime", "os", "queue", "subprocess", "threading", "uuid", "workspace_admin", "safe_project"},
                    "gui/deployment.py": {"ctypes", "os", "uuid", "safe_project", "workspace_admin"},
                    "gui/app.pyw": {"ctypes", "os", "queue", "threading", "tkinter", "service", "deployment"}}
DESKTOP_IMPORTS = {
    "__init__.py": set(), "__main__.py": {"os", "PySide6", "desktop"},
    "core.py": {"dataclasses", "secrets", "threading", "time", "uuid", "workspace_admin", "safe_project", "usage", "materials", "creative", "lengths", "workflow"},
    "bridge.py": {"uuid", "core", "host"}, "usage.py": set(),
    "deployment.py": {"uuid", "core", "gui"},
    "host.py": {"contextlib", "os", "subprocess", "time", "uuid", "core", "usage", "prompting", "api_service", "cache_policy", "aesthetics", "providers", "transcripts"},
    "transcripts.py": {"uuid", "core"},
    "prompting.py": {"dataclasses", "core", "text_metrics", "aesthetics"}, "cache_policy.py": {"decimal", "core"},
    "workflow.py": {"workflow_store", "workflow_planning", "workflow_scale", "workflow_review", "workflow_chapters", "workflow_pipeline", "workflow_execution"},
    "workflow_pipeline.py": {"os", "re", "time", "uuid", "core", "text_metrics", "lengths", "workflow_store", "workflow_requests", "workflow_planning", "workflow_review", "workflow_scale", "workflow_execution"},
    "workflow_execution.py": {"core", "workflow_scale", "workflow_store"},
    "workflow_chapters.py": {"secrets", "time", "uuid", "core", "workflow_store", "workflow_scale", "text_metrics", "lengths"},
    "workflow_review.py": {"core", "material_search", "workflow_store", "workflow_planning"},
    "workflow_store.py": {"ctypes", "os", "time", "uuid", "core"},
    "workflow_requests.py": {"core", "workflow_store"},
    "workflow_planning.py": {"difflib", "os", "re", "secrets", "time", "uuid", "core", "workflow_store", "workflow_requests", "material_formats"},
    "workflow_scale.py": {"math", "core", "lengths", "workflow_store"},
    "creative.py": {"difflib", "uuid", "core", "aesthetics", "style_analysis"}, "lengths.py": {"math", "uuid", "core", "creative", "text_metrics"},
    "aesthetics.py": {"core"},
    "costs.py": {"datetime", "decimal", "core", "cache_policy"},
    "desktop/cost_ui.py": {"PySide6", "core", "costs"},
    "style_analysis.py": {"math", "statistics", "time", "uuid", "core", "creative", "text_metrics", "markdown_it"},
    "exporting.py": {"secrets", "time", "core", "export_formats"},
    "export_formats.py": {"base64", "contextlib", "html", "os", "subprocess", "zipfile", "core", "text_metrics", "PySide6", "docx", "markdown_it", "secure_read", "material_formats", "lxml"},
    "text_metrics.py": {"functools", "unicodedata", "core", "novelctl", "markdown_it"},
    "credentials.py": {"ctypes", "os", "uuid", "core"},
    "providers.py": {"contextlib", "logging", "ssl", "time", "core", "cache_policy", "certifi", "httpx", "httpx2", "openai", "anthropic", "google"},
    "api_service.py": {"time", "uuid", "core", "credentials", "providers", "cache_policy", "prompting", "aesthetics"},
    "desktop/__init__.py": set(), "desktop/demo.py": set(),
    "desktop/lifecycle.py": {"gc", "PySide6"},
    "desktop/theme.py": {"PySide6", "ui_locale"}, "desktop/ui_locale.py": {"PySide6"}, "desktop/window.py": {"base64", "PySide6", "demo", "theme", "", "material_ui", "text_metrics"},
    "desktop/dialogs.py": {"PySide6"},
    "desktop/presenter.py": {"uuid", "PySide6", "core", "host", "usage", "dialogs", "gui", "deployment", "material_ui", "api_service", "providers", "provider_ui", "creative_ui", "lengths", "style_analysis_ui", "export_ui", "transcript_ui", "cost_ui"},
    "desktop/transcript_ui.py": {"PySide6"},
    "desktop/export_ui.py": {"PySide6", "core", "exporting", "dialogs", "presenter"},
    "desktop/creative_ui.py": {"PySide6", "core", "creative", "lengths", "text_metrics", "dialogs", "aesthetics"},
    "desktop/style_analysis_ui.py": {"PySide6", "core", "style_analysis", "creative", "providers", "creative_ui", "dialogs", "presenter"},
    "desktop/provider_ui.py": {"PySide6", "api_service", "providers", "core", "dialogs", "presenter"},
    "materials.py": {"dataclasses", "difflib", "os", "secrets", "subprocess", "time", "uuid", "core", "material_formats", "material_search"},
    "material_formats.py": {"io", "posixpath", "urllib", "zipfile", "lxml", "docx", "pypdf", "striprtf", "markdown_it"},
    "material_search.py": {"sqlite3"},
    "parse_worker.py": {"ctypes", "os", "resource", "novel_studio"},
    "desktop/material_ui.py": {"PySide6", "core", "materials", "material_formats", "material_search", "dialogs"},
}
DESKTOP_IMPORTS["desktop/window.py"].add("workflow_ui")
DESKTOP_IMPORTS["desktop/window.py"].add("shell")
DESKTOP_IMPORTS["desktop/shell.py"] = {"PySide6", "chrome", "theme", "export_ui"}
DESKTOP_IMPORTS["desktop/chrome.py"] = {"PySide6"}
DESKTOP_IMPORTS["desktop/ui_state.py"] = {"core"}
DESKTOP_IMPORTS["desktop/workspace.py"] = {"PySide6", "core", "workflow_review", "workflow_ui", "ui_state"}
DESKTOP_IMPORTS["desktop/presenter.py"].add("workflow_controller")
DESKTOP_IMPORTS["desktop/presenter.py"].add("ui_state")
DESKTOP_IMPORTS["desktop/transcript_ui.py"].add("ui_state")
DESKTOP_IMPORTS["diagnostics.py"] = {"logging", "time", "uuid", "core"}
DESKTOP_IMPORTS["desktop/diagnostic_ui.py"] = {"PySide6", "core", "dialogs", "time"}
DESKTOP_IMPORTS["desktop/presenter.py"].update({"diagnostics", "diagnostic_ui"})
DESKTOP_IMPORTS["__main__.py"].add("diagnostics")
DESKTOP_IMPORTS["core.py"].add("diagnostics")
DESKTOP_IMPORTS["workflow_requests.py"].add("diagnostics")
DESKTOP_IMPORTS["workflow_requests.py"].add("prompting")
DESKTOP_IMPORTS["providers.py"].update({"model_limits", "prompting"})
DESKTOP_IMPORTS["api_service.py"].add("model_limits")
DESKTOP_IMPORTS["desktop/provider_ui.py"].add("model_limits")
DESKTOP_IMPORTS["model_limits.py"] = set()
DESKTOP_IMPORTS["host_diagnostics.py"] = {"core"}
DESKTOP_IMPORTS["host.py"].add("host_diagnostics")
DESKTOP_IMPORTS["diagnostics.py"].update({"contextlib", "contextvars", "host_diagnostics"})
DESKTOP_IMPORTS["desktop/task_scope.py"] = set()
DESKTOP_IMPORTS["desktop/workspace.py"].add("task_scope")
DESKTOP_IMPORTS['document_titles.py'] = set()
DESKTOP_IMPORTS['exporting.py'].update({'document_titles', 'text_metrics'})
DESKTOP_IMPORTS['export_formats.py'].add('document_titles')
DESKTOP_IMPORTS['desktop/context_menu.py'] = {'PySide6'}
DESKTOP_IMPORTS['desktop/export_selection.py'] = {'PySide6'}
DESKTOP_IMPORTS['desktop/object_actions.py'] = {'context_menu'}
DESKTOP_IMPORTS['desktop/object_controller.py'] = {'PySide6', 'core', 'dialogs', 'difflib', 'export_ui', 'materials', 'workflow_controller', 'project_backup', 'context_menu'}
DESKTOP_IMPORTS['project_backup.py'] = {'io', 'zipfile', 'zlib', 'core'}
DESKTOP_IMPORTS['desktop/interactions.py'] = {'PySide6', 'context_menu', 'object_actions', 'ui_state'}
DESKTOP_IMPORTS['desktop/export_ui.py'].update({'export_selection', 'context_menu', 'export_formats'})
DESKTOP_IMPORTS['desktop/workspace.py'].update({'context_menu', 'object_actions'})
DESKTOP_IMPORTS['desktop/shell.py'].add('interactions')
DESKTOP_IMPORTS['desktop/theme.py'].add('core')
DESKTOP_IMPORTS['desktop/presenter.py'].add('object_controller')
DESKTOP_IMPORTS["workflow_store.py"].add("diagnostics")
DESKTOP_IMPORTS["workflow_pipeline.py"].update({"context_budget", "diagnostics"})
DESKTOP_IMPORTS["context_budget.py"] = {"core", "prompting"}
DESKTOP_IMPORTS["workflow.py"].add("workflow_author")
DESKTOP_IMPORTS["workflow_pipeline.py"].add("workflow_plans")
DESKTOP_IMPORTS["workflow_author.py"] = {"time", "uuid", "core", "text_metrics", "workflow_store", "workflow_requests", "workflow_plans", "workflow_review", "workflow_pipeline"}
DESKTOP_IMPORTS["workflow_plans.py"] = {"core", "workflow_planning"}
DESKTOP_IMPORTS["desktop/chapter_tree.py"] = {"PySide6", 'document_titles'}
DESKTOP_IMPORTS["desktop/workspace.py"].update({"workflow_plans", "chapter_tree"})
DESKTOP_IMPORTS.update({
    "desktop/workflow_ui.py": {"PySide6", "core", "workflow_scale", "workflow_planning", "workspace"},
    "desktop/workflow_controller.py": {"PySide6", "core", "workflow_review", "workflow_ui", "dialogs", "workflow_guidance_ui", "export_ui", "task_scope", 'object_controller'},
    "desktop/workflow_guidance_ui.py": {"PySide6", "workflow_ui"},
})
REVIEWED_IMPORTS.update({"src/novel_studio/" + name: imports for name, imports in DESKTOP_IMPORTS.items()})
REVIEWED_IMPORTS['gui/service.py'].add('novel_studio')
REVIEWED_IMPORTS['src/novel_studio/host.py'].add('compatibility')
REVIEWED_IMPORTS['src/novel_studio/core.py'].update({'schema_migration', 'version_info'})
REVIEWED_IMPORTS['src/novel_studio/workflow_store.py'].add('version_info')
REVIEWED_IMPORTS['src/novel_studio/desktop/presenter.py'].add('compatibility_ui')
REVIEWED_IMPORTS.update({
    'src/novel_studio/compatibility.py': {'os', 'threading', 'time', 'core', 'compatibility_store', 'version_info'},
    'src/novel_studio/compatibility_store.py': {'ctypes', 'os', 'core'},
    'src/novel_studio/compatibility_probe.py': {'uuid', 'core', 'compatibility'},
    'src/novel_studio/version_info.py': {''},
    'src/novel_studio/schema_migration.py': {'difflib', 'secrets', 'time', 'uuid', 'core', 'workflow_store'},
    'src/novel_studio/desktop/compatibility_ui.py': {'PySide6', 'core', 'version_info', 'compatibility_probe', 'compatibility_store', '', 'dialogs'},
})
REVIEWED_IMPORTS["desktop/desktop-entry.pyw"] = {"novel_studio", "ctypes", "os"}
REVIEWED_IMPORTS["desktop/api-setup.pyw"] = {"os", "PySide6", "novel_studio", "gui"}
# Accepted B01/B02/B07 additions. Keep reviews tied to individual source files.
REVIEWED_IMPORTS['src/novel_studio/core.py'].update({'snapshot_history', 'canonical_recovery'})
REVIEWED_IMPORTS['src/novel_studio/core.py'].add('recovery_extensions')
REVIEWED_IMPORTS['src/novel_studio/__main__.py'].add('gui')
REVIEWED_IMPORTS['src/novel_studio/desktop/presenter.py'].update({'canonical_recovery_ui', 'local_config_ui'})
REVIEWED_IMPORTS.update({
    'src/novel_studio/snapshot_history.py': {'secrets', 'time', 'core'},
    'src/novel_studio/canonical_recovery.py': {'contextlib', 'copy', 'secrets', 'shutil', 'time', 'uuid',
                                            'core', 'workflow_store', 'lengths', 'text_metrics'},
    'src/novel_studio/recovery_extensions.py': {'copy', 'secrets', 'time', 'uuid', 'canonical_recovery', 'workflow_store'},
    'src/novel_studio/desktop/canonical_recovery_ui.py': {'PySide6', 'core'},
    'src/novel_studio/desktop/local_config_ui.py': {'PySide6'},
})
# B04: shared display, worker and author-confirmation contracts replace imports
# of their upper-level consumers. Public facade re-exports remain compatible.
REVIEWED_IMPORTS.update({
    'src/novel_studio/desktop/workflow_display.py': {'PySide6'},
    'src/novel_studio/desktop/jobs.py': {'PySide6', 'diagnostics'},
    'src/novel_studio/desktop/author_approval.py': {'PySide6', 'dialogs'},
})
REVIEWED_IMPORTS['src/novel_studio/desktop/workflow_ui.py'].add('workflow_display')
REVIEWED_IMPORTS['src/novel_studio/desktop/workspace.py'].discard('workflow_ui')
REVIEWED_IMPORTS['src/novel_studio/desktop/workspace.py'].add('workflow_display')
REVIEWED_IMPORTS['src/novel_studio/desktop/workflow_guidance_ui.py'] = {'PySide6', 'workflow_display'}
for _view in ('presenter', 'export_ui', 'provider_ui', 'style_analysis_ui'):
    REVIEWED_IMPORTS['src/novel_studio/desktop/' + _view + '.py'].add('jobs')
    REVIEWED_IMPORTS['src/novel_studio/desktop/' + _view + '.py'].discard('presenter')
REVIEWED_IMPORTS['src/novel_studio/desktop/workflow_controller.py'].add('author_approval')
# B06: process-local derived caches; no file/network I/O or serialized authority.
REVIEWED_IMPORTS['src/novel_studio/core.py'].update({'collections', 'read_cache', 'material_search', 'text_metrics'})
REVIEWED_IMPORTS['src/novel_studio/read_cache.py'] = {'collections', 'copy', 'threading'}
REVIEWED_IMPORTS['src/novel_studio/material_search.py'].update({'collections', 'threading'})
REVIEWED_IMPORTS['src/novel_studio/desktop/interactions.py'].add('core')
REVIEWED_IMPORTS['src/novel_studio/desktop/presenter.py'].add('threading')
REVIEWED_IMPORTS['src/novel_studio/desktop/object_controller.py'].discard('workflow_controller')
REVIEWED_IMPORTS['src/novel_studio/desktop/object_controller.py'].add('author_approval')
# First-use Qt dialogs: local folder selection and a read-only bundled example.
REVIEWED_IMPORTS['src/novel_studio/desktop/project_ui.py'] = {'PySide6', 'core', 'demo', 'workflow_display'}
REVIEWED_IMPORTS['src/novel_studio/desktop/workspace.py'].update({'demo', 'project_ui'})
REVIEWED_IMPORTS['src/novel_studio/desktop/presenter.py'].add('project_ui')
REVIEWED_IMPORTS['src/novel_studio/desktop/first_ai.py'] = {'PySide6', 'core', 'providers', 'provider_ui', 'workflow_display'}
REVIEWED_IMPORTS['src/novel_studio/desktop/workflow_controller.py'].add('first_ai')
# Reviewed, immutable bundle-verification output: no npm scripts/dependencies.
# This is a byte-preserving historical record, not a general docs exception.
# Never trust a replacement file's own manifest to authorize its new checksum.
READ_ONLY_TEST_EVIDENCE = {
    'docs/versions/1.3/testing/long-term-compatibility/package.json':
        (1121, '39dd7d772da597eef48a83e9901ed7a7d35d7fcb37c5d95a14060770f417c631'),
}
AUTHOR_IO_CALLS = {"os.mkdir", "os.unlink", "os.rename", "os.replace", "os.link", "stream.write"}
MUTATIONS = {"write_text", "write_bytes", "unlink", "remove", "rmdir", "rmtree", "rename", "replace", "mkdir", "makedirs", "chmod", "chown", "touch", "symlink_to", "link_to", "hardlink_to", "truncate"}
EXECUTION = {"eval", "exec", "compile", "__import__", "system", "popen", "spawn", "connect", "urlopen", "urlretrieve"}


def constant_text(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp):
        left, right = constant_text(node.left), constant_text(node.right)
        if isinstance(node.op, ast.Add) and left is not None and right is not None:
            return left + right
        if isinstance(node.op, ast.Mult) and left is not None and isinstance(node.right, ast.Constant) and isinstance(node.right.value, int) and 0 <= node.right.value <= 10000:
            return left * node.right.value
    return None


def scan_python(text: str, label: str) -> list[str]:
    findings = []
    try:
        tree = ast.parse(text, filename=label)
    except SyntaxError:
        return [label + ": invalid Python"]
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [item.name for item in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for name in names:
                if name.split(".")[0] not in ALLOWED_IMPORTS | REVIEWED_IMPORTS.get(label, set()):
                    findings.append(f"{label}:{node.lineno}: unreviewed import {name}")
                if label == "src/novel_studio/material_formats.py" and name.startswith("urllib") and name != "urllib.parse":
                    findings.append(f"{label}:{node.lineno}: only URL parsing, never network access, is reviewed")
        if isinstance(node, ast.Call):
            target = node.func
            name = target.id if isinstance(target, ast.Name) else target.attr if isinstance(target, ast.Attribute) else ""
            regex_compile = name == "compile" and isinstance(target, ast.Attribute) and ast.unparse(target.value) == "re"
            string_replace = name == "replace" and isinstance(target, ast.Attribute) and ast.unparse(target.value) not in {"os", "Path"} and len(node.args) in {2, 3} and all(isinstance(arg, ast.Constant) and isinstance(arg.value, str) for arg in node.args[:2]) and not node.keywords
            verified_bytecode = label == "scripts/novelctl.py" and ((name == "compile" and len(node.args) == 3 and ast.unparse(node.args[0]) == "payload" and isinstance(node.args[2], ast.Constant) and node.args[2].value == "exec") or (name == "exec" and len(node.args) == 2 and ast.unparse(node.args[0]) == "code" and ast.unparse(node.args[1]) == "module.__dict__"))
            reviewed_author_io = ((label == "scripts/safe_project.py" and ast.unparse(target) in AUTHOR_IO_CALLS)
                                  or (label == "scripts/workspace_admin.py" and ast.unparse(target) in {"tree.mkdir", "parent.mkdir"})
                                  or (label == "gui/service.py" and ast.unparse(target) in {"tree.mkdir", "parent.mkdir"})
                                  or (label == "gui/deployment.py" and ast.unparse(target) in {"tree.mkdir", "target.mkdir"})
                                  or (label == "gui/app.pyw" and ast.unparse(target) == "tree.mkdir")
                                  or (label in {"src/novel_studio/core.py", "src/novel_studio/host.py", "src/novel_studio/api_service.py", "src/novel_studio/creative.py", "src/novel_studio/style_analysis.py", "src/novel_studio/transcripts.py", "src/novel_studio/workflow_store.py"} and ast.unparse(target) == "tree.mkdir")
                                  or (label == "src/novel_studio/deployment.py" and ast.unparse(target) in {"tree.mkdir", "parent.mkdir", "target.mkdir"})
                                  or (label == "src/novel_studio/diagnostics.py" and ast.unparse(target) in {"tree.mkdir", "parent.mkdir"})
                                  or (label == "src/novel_studio/host.py" and ast.unparse(target) == "process.stdin.write"))
            if label == "src/novel_studio/materials.py" and ast.unparse(target) == "tree.mkdir":
                reviewed_author_io = True
            qt_event_loop = (name == "exec" and label in {"src/novel_studio/desktop/diagnostic_ui.py", "src/novel_studio/__main__.py", "src/novel_studio/desktop/presenter.py", "src/novel_studio/desktop/material_ui.py", "src/novel_studio/desktop/provider_ui.py", "desktop/api-setup.pyw", "src/novel_studio/desktop/creative_ui.py", "src/novel_studio/desktop/style_analysis_ui.py", "src/novel_studio/desktop/export_ui.py"}
                             and (ast.unparse(target) in {"app.exec", "dialog.exec", "wizard.exec"} or
                                  isinstance(target, ast.Attribute) and isinstance(target.value, ast.Call) and isinstance(target.value.func, ast.Name) and target.value.func.id in {"DiffDialog", "InformationDialog", "ProviderDialog", "WriterSettingsDialog", "EditorSettingsDialog", "StyleAnalysisDialog", "ExportDialog", "TranscriptDialog", "CostDialog"}))
            qt_signal = name == "connect" and label in {"src/novel_studio/desktop/diagnostic_ui.py", "src/novel_studio/desktop/window.py", "src/novel_studio/desktop/dialogs.py", "src/novel_studio/desktop/presenter.py", "src/novel_studio/desktop/material_ui.py", "src/novel_studio/desktop/provider_ui.py", "src/novel_studio/desktop/creative_ui.py", "src/novel_studio/desktop/style_analysis_ui.py", "src/novel_studio/desktop/export_ui.py"} and isinstance(target, ast.Attribute) and isinstance(target.value, ast.Attribute) and target.value.attr in {"clicked", "toggled", "accepted", "rejected", "textChanged", "itemClicked", "activated", "modificationChanged", "triggered", "action_requested", "layout_changed", "progress", "finished_value", "failed", "finished", "ready", "returnPressed", "itemSelectionChanged", "currentRowChanged", "itemChanged", "currentIndexChanged", "currentTextChanged", "currentItemChanged", "timeout", "valueChanged"}
            memory_search = label == "src/novel_studio/material_search.py" and ast.unparse(target) == "sqlite3.connect" and len(node.args) == 1 and isinstance(node.args[0], ast.Constant) and node.args[0].value == ":memory:"
            if label in {'src/novel_studio/compatibility.py', 'src/novel_studio/compatibility_store.py', 'src/novel_studio/compatibility_probe.py', 'src/novel_studio/schema_migration.py', 'src/novel_studio/desktop/compatibility_ui.py'}:
                reviewed_author_io = reviewed_author_io or ast.unparse(target) in {'tree.mkdir', 'parent.mkdir'}
            if label in {'src/novel_studio/canonical_recovery.py', 'src/novel_studio/recovery_extensions.py'} and ast.unparse(target) == 'tree.mkdir':
                reviewed_author_io = True
            if label in {'src/novel_studio/desktop/canonical_recovery_ui.py', 'src/novel_studio/desktop/local_config_ui.py'}:
                qt_signal = (name == 'connect' and isinstance(target, ast.Attribute)
                             and isinstance(target.value, ast.Attribute)
                             and target.value.attr in {'clicked', 'toggled', 'currentRowChanged', 'accepted', 'rejected'})
            if (label == 'src/novel_studio/desktop/local_config_ui.py' and ast.unparse(target) == 'dialog.exec'
                    or label == 'src/novel_studio/desktop/presenter.py' and ast.unparse(target) == 'RecoveryDialog(self).exec'
                    or label == 'src/novel_studio/desktop/canonical_recovery_ui.py' and ast.unparse(target) == 'PhaseTwoDialog(self.p).exec'):
                qt_event_loop = not node.args and not node.keywords
            if label == 'src/novel_studio/desktop/object_controller.py' and ast.unparse(target) == 'files.currentIndexChanged.connect':
                qt_signal = True
            if (label == 'src/novel_studio/desktop/author_approval.py'
                    and ast.unparse(target) == 'DiffDialog(preview, presenter.window).exec'
                    and not node.args and not node.keywords):
                qt_event_loop = True
            if label == 'src/novel_studio/desktop/compatibility_ui.py':
                qt_signal = name == 'connect' and isinstance(target, ast.Attribute) and isinstance(target.value, ast.Attribute) and target.value.attr == 'clicked'
                qt_event_loop = ast.unparse(target) == 'dialog.exec'
            if label == 'src/novel_studio/desktop/presenter.py' and ast.unparse(target) == 'CompatibilityDialog(self).exec':
                qt_event_loop = True
            if label in {"src/novel_studio/desktop/workflow_ui.py", "src/novel_studio/desktop/workflow_guidance_ui.py"}:
                qt_signal = qt_signal or (name == "connect" and isinstance(target, ast.Attribute) and isinstance(target.value, ast.Attribute)
                    and target.value.attr in {"clicked", "accepted", "rejected", "toggled", "valueChanged", "currentIndexChanged", "currentItemChanged", "timeout", "finished"})
            if label == "src/novel_studio/desktop/workflow_controller.py":
                qt_event_loop = name == "exec" and (ast.unparse(target) == "dialog.exec" or isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Call) and isinstance(target.value.func, ast.Name)
                    and target.value.func.id in {"ScaleDialog", "InformationDialog", "DiffDialog", "GuidanceDialog"})
            if label in {"src/novel_studio/desktop/shell.py", "src/novel_studio/desktop/chrome.py"}:
                qt_signal = name == "connect" and isinstance(target, ast.Attribute) and isinstance(target.value, ast.Attribute) and target.value.attr in {
                    "clicked", "finished", "settled", "selected", "currentChanged", "currentIndexChanged", "splitterMoved", "currentRowChanged", "activated", "triggered", "valueChanged", "extent_changed", "view_changed", "selection_changed", "aboutToShow", "toggled"}
            if label == "src/novel_studio/desktop/workspace.py":
                qt_signal = name == "connect" and isinstance(target, ast.Attribute) and isinstance(target.value, ast.Attribute) and target.value.attr in {
                    "clicked", "triggered", "currentChanged", "itemClicked", "currentRowChanged", "currentIndexChanged", "valueChanged", "textChanged", "accepted", "rejected", "itemSelectionChanged", "itemChanged"}
                qt_signal = qt_signal or ast.unparse(target) == "self.recent_projects.itemActivated.connect"
                qt_event_loop = name == "exec" and ast.unparse(target) == "dialog.exec"
            if label == "src/novel_studio/desktop/project_ui.py":
                qt_signal = ast.unparse(target) in {"choose.clicked.connect", "buttons.accepted.connect", "buttons.rejected.connect",
                                                    "self.title.textChanged.connect", "self.folder.textChanged.connect"}
            if label == "src/novel_studio/desktop/first_ai.py":
                qt_signal = ast.unparse(target) in {"choose.clicked.connect", "close.clicked.connect", "self.check.clicked.connect",
                                                    "self.proceed.clicked.connect", "self.idea.textChanged.connect"}
                qt_event_loop = ast.unparse(target) == "dialog.exec" and not node.args and not node.keywords
            if label in {"src/novel_studio/desktop/transcript_ui.py", "src/novel_studio/desktop/cost_ui.py"} and name == "connect" and isinstance(target, ast.Attribute) and isinstance(target.value, ast.Attribute) and target.value.attr == "clicked":
                qt_signal = True
            if label in {'src/novel_studio/desktop/context_menu.py', 'src/novel_studio/desktop/export_selection.py', 'src/novel_studio/desktop/interactions.py', 'src/novel_studio/desktop/object_controller.py', 'src/novel_studio/desktop/export_ui.py', 'src/novel_studio/desktop/workspace.py'}:
                if name == 'connect' and isinstance(target, ast.Attribute) and isinstance(target.value, ast.Attribute) and target.value.attr in {'clicked', 'triggered', 'customContextMenuRequested', 'currentItemChanged', 'itemChanged', 'currentRowChanged', 'selection_changed', 'selectionChanged', 'cursorPositionChanged', 'view_changed', 'choices_changed', 'aboutToShow'}:
                    qt_signal = True
            if label in {'src/novel_studio/desktop/context_menu.py', 'src/novel_studio/desktop/interactions.py'} and ast.unparse(target) == 'menu.exec':
                qt_event_loop = True
            if label == 'src/novel_studio/desktop/object_controller.py' and name == 'exec' and (ast.unparse(target) == 'dialog.exec' or isinstance(target, ast.Attribute) and isinstance(target.value, ast.Call) and isinstance(target.value.func, ast.Name) and target.value.func.id == 'InformationDialog'):
                qt_event_loop = True
            if (name in MUTATIONS and not string_replace and not reviewed_author_io) or (name in EXECUTION and not regex_compile and not verified_bytecode and not qt_event_loop and not qt_signal and not memory_search):
                findings.append(f"{label}:{node.lineno}: forbidden runtime operation {name}")
            if name == "exec_module" and label != "scripts/novelctl.py":
                findings.append(f"{label}:{node.lineno}: unreviewed dynamic loading")
            parser_stdout = label == "src/novel_studio/parse_worker.py" and ast.unparse(target) == "sys.stdout.buffer.write"
            qt_odt_buffer = label == "src/novel_studio/export_formats.py" and name == "write" and isinstance(target, ast.Attribute) and isinstance(target.value, ast.Call) and isinstance(target.value.func, ast.Name) and target.value.func.id == "QTextDocumentWriter"
            if name == "write" and not reviewed_author_io and not parser_stdout and not qt_odt_buffer and not (isinstance(target, ast.Attribute) and ast.unparse(target.value) in {"sys.stdout", "sys.stderr"}):
                findings.append(f"{label}:{node.lineno}: file/stream write needs review")
            descriptor_open = label in {"scripts/secure_read.py", "scripts/safe_project.py"} and isinstance(target, ast.Attribute) and ast.unparse(target) == "os.open"
            # LocalStore.open(root, recovery_callback) opens a validated state
            # service, not a file with a mode. Other open calls remain checked.
            config_store_open = (label in {'gui/service.py', 'src/novel_studio/desktop/presenter.py'}
                                 and ast.unparse(target) == 'LocalStore.open'
                                 and len(node.args) == 2 and not node.keywords)
            if name == "open" and not descriptor_open and not config_store_open:
                mode = next((kw.value for kw in node.keywords if kw.arg == "mode"), None)
                if mode is None and len(node.args) > 1:
                    mode = node.args[1]
                if mode is not None and (not isinstance(mode, ast.Constant) or not isinstance(mode.value, str) or mode.value not in {"r", "rt", "rb"}):
                    findings.append(f"{label}:{node.lineno}: non-read-only open")
        value = constant_text(node)
        if value is not None:
            official_endpoint = label in {"src/novel_studio/providers.py", "scripts/security_check.py"} and value in {"https://api.openai.com/v1", "https://api.anthropic.com", "https://generativelanguage.googleapis.com"}
            xml_namespace = label in {"src/novel_studio/export_formats.py", "scripts/security_check.py"} and value == "{http://www.idpf.org/2007/opf}"
            if re.search(r"https?://", value) and not official_endpoint and not xml_namespace:
                findings.append(f"{label}:{getattr(node, 'lineno', 0)}: runtime endpoint requires review")
            if re.search(r"(?:sk-|AIza)[A-Za-z0-9_-]{20,}", value):
                findings.append(f"{label}:{getattr(node, 'lineno', 0)}: possible embedded credential")
    return sorted(set(findings))


def reviewed_test_evidence(path: Path, label: str) -> bool:
    record = READ_ONLY_TEST_EVIDENCE.get(label)
    if record is None:
        return False
    if any(p.is_symlink() or getattr(p, 'is_junction', lambda: False)() for p in (path, *path.parents)):
        return False
    size, expected = record
    return path.stat().st_size == size and hashlib.sha256(path.read_bytes()).hexdigest() == expected


def check_repository(root: Path = ROOT) -> list[str]:
    findings = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if relative.parts[0] in {".venv-workbench", ".build", "build"} or any(part in {".git", "__pycache__", ".venv"} for part in relative.parts) or not path.is_file():
            continue
        if path.suffix.lower() in {".js", ".mjs", ".cjs", ".ts", ".sh", ".ps1", ".bat", ".cmd", ".exe", ".dll"} or path.name in {"Dockerfile", "docker-compose.yml", "compose.yaml", "package.json"}:
            if not reviewed_test_evidence(path, relative.as_posix()):
                findings.append(relative.as_posix() + ": unreviewed executable/service surface")
        if path.suffix in {".py", ".pyw"} and relative.parts[0] not in {"scripts", "vendor", "tests"} and relative.as_posix() not in REVIEWED_IMPORTS and relative.as_posix() not in {"tools/build_plugin.py", "tools/build_windows_gui.py", "tools/build_workbench.py", "tools/build_selection_icons.py", "tools/build_public_windows.py"}:
            findings.append(relative.as_posix() + ": unreviewed Python surface")
    for name in ("hooks.json", "mcp_config.json"):
        if (root / name).exists():
            findings.append(name + ": not allowed in this prototype")
    if (root / ".gitmodules").exists():
        findings.append("Unreviewed Git submodules")
    lock = json.loads((root / "integrations.lock.json").read_text(encoding="utf-8"))
    expected = set()
    for row in lock["files"]:
        relative = row["destination"]
        candidate = Path(relative)
        if candidate.is_absolute() or candidate.drive or ".." in candidate.parts or not candidate.parts or candidate.parts[0] != "vendor":
            findings.append("Invalid vendor path in lock")
            continue
        path = root / candidate
        if any(p.is_symlink() or getattr(p, "is_junction", lambda: False)() for p in (path, *path.parents)):
            findings.append(relative + ": linked vendor path")
            continue
        expected.add(relative)
        if not path.is_file():
            findings.append(relative + ": missing upstream file")
            continue
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != row["sha256"]:
            findings.append(relative + ": upstream checksum mismatch")
        blob = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
        if blob != row["blob_sha"] or not re.fullmatch(r"[a-f0-9]{40}", row["commit"]):
            findings.append(relative + ": upstream provenance mismatch")
        if row["license"] != "MIT" or row["modified"] or row["modifications"]:
            findings.append(relative + ": unapproved license/modification")
        if row["runtime_dependencies"] not in ([], ["Python standard library"]):
            findings.append(relative + ": unreviewed dependency")
    actual = {p.relative_to(root).as_posix() for p in (root / "vendor").rglob("*") if p.is_file()}
    if actual != expected:
        findings.append("Vendor inventory differs from reviewed lock")
    for folder in ("scripts", "vendor", "gui", "src", "desktop"):
        if (root / folder).exists():
            for path in (root / folder).rglob("*"):
                if path.suffix in {".py", ".pyw"}:
                    findings.extend(scan_python(path.read_text(encoding="utf-8"), path.relative_to(root).as_posix()))
    manifest = json.loads((root / "plugin.json").read_text(encoding="utf-8"))
    if "extensions" in manifest or "skills" in manifest:
        findings.append("Extra discovery/customization needs review")
    editor = (root / "agents/novel-editor.md").read_text(encoding="utf-8")
    header = editor.split("---", 2)[1]
    tools = re.findall(r"^  - ([a-z_]+)$", header, re.M)
    if set(tools) != {"view_file", "grep_search", "find_by_name", "write_to_file", "finish"} or "inheritMcp: false" not in header or "subagent: false" not in header:
        findings.append("Editor tool permissions require review")
    return sorted(set(findings))


def main() -> int:
    try:
        findings = check_repository()
    except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        print("FAIL: repository gate could not complete: " + str(exc))
        return 2
    if findings:
        print("\n".join(findings))
        return 1
    print("PASS: offline static gate; manual review and host validation remain required")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
