"""Author-facing coordination only; none of these methods enter Bridge grants."""
from .workflow_store import WorkflowStore
from .workflow_planning import PlanningService
from .workflow_scale import ScaleService
from .workflow_review import GuidanceService
from .workflow_chapters import ChapterService
from .workflow_pipeline import PipelineService
from .workflow_execution import ExecutionService
from .workflow_author import AuthorService


class WorkflowService:
    def __init__(self, core):
        self.core = core
        self.store = WorkflowStore(core)

    @property
    def planning(self):
        return PlanningService(self.core, self.store)

    @property
    def scale(self):
        return ScaleService(self.core, self.store)

    @property
    def guidance(self):
        return GuidanceService(self.core, self.store)

    @property
    def chapters(self):
        return ChapterService(self.core, self.store)

    @property
    def execution(self):
        return ExecutionService(self.core, self.store)

    @property
    def pipeline(self):
        return PipelineService(self.core, self.store)

    @property
    def author(self):
        return AuthorService(self.core, self.store)
