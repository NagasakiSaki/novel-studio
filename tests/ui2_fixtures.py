"""Explicit synthetic UI fixtures; fake transport never launches the official CLI."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from novel_studio.core import Core, SafeTree
from novel_studio.workflow_store import DEFAULT_SCALE
from test_workflow_planning import fixture
from test_workflow_pipeline import ScriptedModels


class LocalOnlyCLI:
    def version(self):
        return "synthetic-no-CLI-execution"
    def installed_plugin(self):
        from novel_studio.core import ROOT as runtime
        if (runtime / "payload/novel-studio").exists():
            return runtime / "payload/novel-studio"
        return runtime
    def command(self, *args, **kwargs):
        raise AssertionError("UI-only tests must not issue CLI/network requests")
    def stream(self, *args, **kwargs):
        raise AssertionError("Use the explicit synthetic structured-task fixture")


def project(parent, title="界面二代合成作品", presenter=None, approve=True):
    from novel_studio.core import ROOT as runtime
    plugin = LocalOnlyCLI().installed_plugin()
    core = Core.create(Path(parent), title, plugin)
    with SafeTree(core.project) as tree:
        tree.create("Canonical/Canon-Worldbuilding/合成设定.md", "雾港的钟楼，林舟修理钟轮；周岚保管蓝信，林舟尚不知道信里内容。".encode())
    if presenter:
        presenter.open_project(core.project)
        core = presenter.core
    else:
        from novel_studio.host import ModelService
        ModelService(core, LocalOnlyCLI())
    models = core.models
    models.save("writer", provider="antigravity", model_id="writer-synthetic")
    models.save("editor", provider="antigravity", model_id="editor-synthetic")
    core.workflow.scale.save({**DEFAULT_SCALE, "total_chapters": 2, "total_volumes": 1, "chapters_per_volume": 2,
        "chapter_target": 40, "chapter_minimum": 10, "chapter_maximum": 200})
    candidate = core.workflow.planning.save(fixture())
    if approve:
        preview = core.workflow.planning.preview(candidate["version_id"])
        core.author_commit(preview["handle"], True)
        core.workflow.chapters.ensure()
    script = ScriptedModels(core)
    core.models = models
    models.structured_task = script.structured_task
    if presenter:
        presenter.refresh_view()
    return core, script


def complete(core):
    run = core.workflow.pipeline.start(core.workflow.pipeline.preview("chapters", 2), True)
    for unused in range(40):
        value = core.workflow.pipeline.advance(run["run_id"])
        if value["status"] == "COMPLETED":
            return value
        assert value["status"] == "RUNNING", value
    raise AssertionError("Synthetic finite pipeline did not complete")
