"""B04 offline import-order tests and a small, classified module dependency check."""
import ast
import importlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
QT = importlib.util.find_spec('PySide6') is not None


def modules(root=ROOT):
    found = {}
    for folder in ('src/novel_studio', 'gui', 'scripts', 'desktop'):
        for path in (root / folder).rglob('*'):
            if path.suffix not in {'.py', '.pyw'} or '__pycache__' in path.parts:
                continue
            parts = list(path.relative_to(root / 'src' if folder.startswith('src/') else root).with_suffix('').parts)
            package = parts[-1] == '__init__'
            if package:
                parts.pop()
            found['.'.join(parts[1:] if folder == 'scripts' else parts)] = (path, package)
    return found


def parse_edges(name, text, known, package=False):
    edges = []
    tree = ast.parse(text)
    future_annotations = any(isinstance(n,ast.ImportFrom) and n.module=='__future__'
                             and any(a.name=='annotations' for a in n.names) for n in tree.body)
    class Imports(ast.NodeVisitor):
        deferred = 0
        typing = False
        type_names = {'TYPE_CHECKING', 'typing.TYPE_CHECKING'}
        dynamic_names = {'importlib.import_module', '__import__'}

        def record(self, target, node, dynamic=False):
            target = {'service':'gui.service', 'deployment':'gui.deployment'}.get(target, target)
            phase = 'type-only' if self.typing else 'deferred' if self.deferred else 'eager'
            if dynamic:
                phase = 'dynamic-' + phase
            edges.append({'source':name,'target':target,'phase':phase,'line':node.lineno,'internal':target in known})

        def visit_Import(self, node):
            for item in node.names:
                self.record(item.name, node)
                if item.name == 'typing':
                    self.type_names.add((item.asname or 'typing') + '.TYPE_CHECKING')
                if item.name == 'importlib':
                    self.dynamic_names.add((item.asname or 'importlib') + '.import_module')

        def visit_ImportFrom(self, node):
            if node.module == 'typing':
                self.type_names.update(item.asname or item.name for item in node.names if item.name == 'TYPE_CHECKING')
            if node.module == 'importlib':
                self.dynamic_names.update(item.asname or item.name for item in node.names if item.name == 'import_module')
            base = node.module or ''
            if node.level:
                prefix = name.split('.') if package else name.split('.')[:-1]
                base = '.'.join(prefix[:len(prefix)-node.level+1] + (base.split('.') if base else []))
            if base:
                self.record(base, node)
            for item in node.names:
                candidate = base + '.' + item.name
                if candidate in known:
                    self.record(candidate, node)

        def visit_FunctionDef(self, node):
            # Defaults/decorators/annotations execute when a function is defined.
            for value in [*node.decorator_list, *node.args.defaults, *[v for v in node.args.kw_defaults if v is not None]]:
                self.visit(value)
            if not future_annotations:
                for argument in [*node.args.posonlyargs,*node.args.args,*node.args.kwonlyargs,node.args.vararg,node.args.kwarg]:
                    if argument is not None and argument.annotation is not None:
                        self.visit(argument.annotation)
                if node.returns is not None:
                    self.visit(node.returns)
            self.deferred += 1
            for statement in node.body:
                self.visit(statement)
            self.deferred -= 1
        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Lambda(self, node):
            for value in [*node.args.defaults,*[v for v in node.args.kw_defaults if v is not None]]:
                self.visit(value)
            self.deferred += 1
            self.visit(node.body)
            self.deferred -= 1

        def visit_If(self, node):
            negated=isinstance(node.test,ast.UnaryOp) and isinstance(node.test.op,ast.Not)
            condition=node.test.operand if negated else node.test
            if ast.unparse(condition) in self.type_names:
                previous = self.typing
                for branch,type_only in ((node.body,not negated),(node.orelse,negated)):
                    self.typing=previous or type_only
                    for statement in branch:
                        self.visit(statement)
                self.typing = previous
            else:
                self.generic_visit(node)

        def visit_Call(self, node):
            target = ast.unparse(node.func)
            if target in self.dynamic_names:
                value = node.args[0] if node.args else None
                self.record(value.value if isinstance(value, ast.Constant) and isinstance(value.value, str) else '<dynamic>', node, True)
            self.generic_visit(node)
    Imports().visit(tree)
    return edges


def graph(root=ROOT):
    known = modules(root)
    return [edge for name,(path,package) in known.items()
            for edge in parse_edges(name,path.read_text(encoding='utf-8'),known,package)]


def cycles(edges, eager=True):
    adjacency = {}
    for edge in edges:
        if not edge['internal'] or 'type-only' in edge['phase']:
            continue
        if eager and edge['phase'] not in {'eager','dynamic-eager'}:
            continue
        adjacency.setdefault(edge['source'],set()).add(edge['target'])
    index, low, stack, active, result = {}, {}, [], set(), []
    def visit(node):
        index[node] = low[node] = len(index)
        stack.append(node)
        active.add(node)
        for target in sorted(adjacency.get(node,())):
            if target not in index:
                visit(target)
                low[node] = min(low[node],low[target])
            elif target in active:
                low[node] = min(low[node],index[target])
        if low[node] == index[node]:
            group = []
            while True:
                target = stack.pop()
                active.remove(target)
                group.append(target)
                if target == node:
                    break
            if len(group)>1 or node in adjacency.get(node,()):
                result.append(sorted(group))
    for node in sorted(adjacency):
        if node not in index:
            visit(node)
    return sorted(result)


def forbidden(edges):
    findings = []
    for edge in edges:
        if 'type-only' in edge['phase']:
            continue
        source, target = edge['source'], edge['target']
        domain = source in {'novel_studio.core','novel_studio.canonical_recovery','novel_studio.snapshot_history'} or source.startswith('novel_studio.workflow')
        storage = source in {'safe_project','secure_read','workspace_admin'}
        if (domain or storage) and (target.startswith(('novel_studio.desktop','gui','PySide6','tkinter'))):
            findings.append(f'{source}:{edge["line"]} -> {target}: forbidden domain/storage to UI dependency')
        if storage and target.startswith('novel_studio'):
            findings.append(f'{source}:{edge["line"]} -> {target}: forbidden storage to business dependency')
        if source == 'novel_studio.desktop.workspace' and target == 'novel_studio.desktop.workflow_ui':
            findings.append('workspace must use workflow display contracts, not the dialog/public facade')
        if source.endswith(('.export_ui','.provider_ui','.style_analysis_ui')) and target == 'novel_studio.desktop.presenter':
            findings.append(f'{source} must use desktop.jobs, not import the Presenter facade')
        if source == 'novel_studio.desktop.object_controller' and target == 'novel_studio.desktop.workflow_controller':
            findings.append('object_controller must use the author approval coordinator, not the workflow dispatcher')
        if source in {'novel_studio.desktop.jobs','novel_studio.desktop.workflow_display','novel_studio.desktop.author_approval'} and target in {
                'novel_studio.desktop.presenter','novel_studio.desktop.workspace','novel_studio.desktop.workflow_ui',
                'novel_studio.desktop.workflow_controller','novel_studio.desktop.object_controller'}:
            findings.append(f'{source} must not import its upper-level consumers: {target}')
    return sorted(set(findings))


class ImportArchitectureTests(unittest.TestCase):
    def fresh_import(self, order):
        with tempfile.TemporaryDirectory(prefix='b04-import-profile-') as profile:
            env = dict(os.environ)
            for key in list(env):
                if key.startswith(('OPENAI_','ANTHROPIC_','GOOGLE_','GEMINI_','AGY_','ANTIGRAVITY_')):
                    env.pop(key)
            env.update(LOCALAPPDATA=profile,APPDATA=profile,HOME=profile,USERPROFILE=profile,QT_QPA_PLATFORM='offscreen',PYTHONIOENCODING='utf-8')
            program = '''
import importlib,importlib.util,json,sys
from pathlib import Path
sys.path[:0]=[sys.argv[1],sys.argv[2]]
def audit(event,args):
    if event in {'socket.connect','subprocess.Popen','os.system','os.mkdir','os.remove','os.rename'}:
        raise AssertionError('unexpected import side effect: '+event)
    if event=='open' and ((isinstance(args[1],str) and any(c in args[1] for c in 'wax+')) or args[2]&0x243):
        raise AssertionError('unexpected write during import')
sys.addaudithook(audit)
order=json.loads(sys.argv[3])
for name in order:
    first=importlib.import_module(name)
    assert importlib.import_module(name) is first
if order==['novel_studio.desktop.export_ui']:
    assert 'novel_studio.desktop.presenter' not in sys.modules, 'export UI imported Presenter only to obtain Job'
if order==['novel_studio.core']:
    assert not any(n.startswith(('novel_studio.desktop','gui.','PySide6')) for n in sys.modules)
if importlib.util.find_spec('PySide6') is not None:
    from PySide6.QtWidgets import QApplication
    assert QApplication.instance() is None
assert not list(Path(sys.argv[4]).rglob('*'))
print('PASS: isolated import, no window/state writes/model requests')
'''
            result = subprocess.run([sys.executable,'-B','-c',program,str(ROOT/'src'),str(ROOT),json.dumps(order),profile],
                                    cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',timeout=40)
            self.assertEqual(result.returncode,0,result.stderr)

    @unittest.skipUnless(QT,'Qt developer/runtime dependency not installed')
    def test_workspace_imports_in_a_fresh_process(self):
        self.fresh_import(['novel_studio.desktop.workspace'])

    @unittest.skipUnless(QT,'Qt developer/runtime dependency not installed')
    def test_export_ui_does_not_import_presenter_to_obtain_a_worker(self):
        self.fresh_import(['novel_studio.desktop.export_ui'])

    def test_core_import_is_independent_of_gui(self):
        self.fresh_import(['novel_studio.core'])

    @unittest.skipUnless(QT,'Qt developer/runtime dependency not installed')
    def test_fresh_import_orders_and_public_entries(self):
        orders = [
            ['novel_studio.core'],
            ['gui.service'],
            ['novel_studio.desktop.workflow_ui','novel_studio.desktop.workspace'],
            ['novel_studio.desktop.export_ui','novel_studio.desktop.presenter','novel_studio.__main__'],
            ['novel_studio.desktop.provider_ui','novel_studio.desktop.style_analysis_ui','novel_studio.desktop.window'],
            ['novel_studio.workflow_store','novel_studio.workflow','novel_studio.core','novel_studio.api_service','gui.service'],
            ['gui.service','novel_studio.api_service','novel_studio.core','novel_studio.workflow','novel_studio.workflow_store'],
            ['novel_studio.project_backup','novel_studio.snapshot_history','novel_studio.canonical_recovery','novel_studio.__main__'],
            ['novel_studio.desktop.jobs','novel_studio.desktop.workflow_display','novel_studio.desktop.author_approval'],
        ]
        for order in orders:
            with self.subTest(order=order):
                self.fresh_import(order)

    def test_no_eager_internal_cycles(self):
        self.assertEqual(cycles(graph()),[])

    def test_key_layer_directions(self):
        self.assertEqual(forbidden(graph()),[])

    def test_detector_distinguishes_deferred_typing_dynamic_and_real_cycles(self):
        known={'demo.a','demo.b'}
        a=parse_edges('demo.a','from . import b',known)
        b=parse_edges('demo.b','from . import a',known)
        self.assertEqual(cycles(a+b),[['demo.a','demo.b']])
        lazy=parse_edges('demo.b','def create():\n from . import a',known)
        self.assertEqual(cycles(a+lazy),[])
        self.assertEqual(cycles(a+lazy,eager=False),[['demo.a','demo.b']])
        typed=parse_edges('demo.b','from typing import TYPE_CHECKING as TC\nif TC:\n from . import a',known)
        self.assertEqual(cycles(a+typed,eager=False),[])
        negative=parse_edges('demo.b','from typing import TYPE_CHECKING as TC\nif not TC:\n pass\nelse:\n from . import a',known)
        self.assertEqual(cycles(a+negative,eager=False),[])
        dynamic=parse_edges('demo.b',"import importlib\nimportlib.import_module('demo.a')",known)
        self.assertEqual(cycles(a+dynamic),[['demo.a','demo.b']])
        bad=parse_edges('novel_studio.workflow_store','def save():\n from .desktop import presenter',{'novel_studio.desktop.presenter'})
        self.assertTrue(forbidden(bad))

    def test_detector_handles_aliases_class_bodies_defaults_and_annotations(self):
        known={'demo.a','demo.b'}
        a=parse_edges('demo.a','from . import b',known)
        for text in ("from importlib import import_module as load\nload('demo.a')",
                     'class View:\n from . import a',
                     'def make(value=__import__("demo.a")):\n pass',
                     'def make(value: __import__("demo.a")):\n pass'):
            self.assertEqual(cycles(a+parse_edges('demo.b',text,known)),[['demo.a','demo.b']])
        deferred='from __future__ import annotations\ndef make(value: __import__("demo.a")):\n pass'
        self.assertEqual(cycles(a+parse_edges('demo.b',deferred,known)),[])
        callback=parse_edges('demo.b',"load=lambda: __import__('demo.a')",known)
        self.assertEqual(cycles(a+callback),[])
        self.assertEqual(cycles(a+callback,eager=False),[['demo.a','demo.b']])


@unittest.skipUnless(QT,'Qt developer/runtime dependency not installed')
class SharedUIContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
        from PySide6.QtWidgets import QApplication
        cls.app=QApplication.instance() or QApplication([])

    def test_display_facade_and_panel_exports_remain_identical(self):
        from PySide6.QtCore import Qt
        from novel_studio.desktop import workflow_display as display, workflow_ui, workspace
        for name in ('plain','model_label','STATUS','PHASE'):
            self.assertIs(getattr(workflow_ui,name),getattr(display,name))
        self.assertIs(workflow_ui.WorkflowPanel,workspace.WorkflowPanel)
        label=display.plain('<b>synthetic text</b>','muted')
        self.assertEqual(label.textFormat(),Qt.PlainText)
        self.assertTrue(label.wordWrap())
        self.assertEqual(label.objectName(),'muted')
        label.deleteLater()
        selected=Mock(return_value={'provider':'openai','model_id':'synthetic-local-label'})
        core=SimpleNamespace(models=SimpleNamespace(selected=selected))
        self.assertEqual(display.model_label(core,'editor'),'OpenAI · synthetic-local-label')
        selected.assert_called_once_with('editor')

    def test_worker_alias_progress_completion_and_diagnostic_scope(self):
        from novel_studio.desktop.jobs import Job
        from novel_studio.desktop.presenter import Job as old
        from novel_studio import diagnostics
        self.assertIs(Job,old)
        received,progress,failed=[],[],[]
        def operation(notify):
            notify({'step':'synthetic'})
            return dict(diagnostics._SCOPE.get())
        job=Job(operation)
        job.diagnostic_context={'action':'b04_synthetic','role':'writer'}
        job.finished_value.connect(received.append)
        job.progress.connect(progress.append)
        job.failed.connect(failed.append)
        job.start()
        self.assertTrue(job.wait(5000))
        self.app.processEvents()
        self.assertEqual(received,[job.diagnostic_context])
        self.assertEqual(progress,[{'step':'synthetic'}])
        self.assertEqual(failed,[])
        self.assertIsNone(diagnostics._SCOPE.get())
        job.deleteLater()

    def test_worker_error_stays_error_not_success(self):
        from novel_studio.desktop.jobs import Job
        error=ValueError('synthetic operation failed')
        def operation(notify):
            raise error
        job=Job(operation)
        failed,done=[],[]
        job.failed.connect(failed.append)
        job.finished_value.connect(done.append)
        job.start()
        self.assertTrue(job.wait(5000))
        self.app.processEvents()
        self.assertEqual(failed,[error])
        self.assertEqual(done,[])
        job.deleteLater()

    def test_author_approval_alias_confirmation_and_recovery_callback(self):
        from PySide6.QtWidgets import QDialog
        from novel_studio.desktop import author_approval, workflow_controller
        from novel_studio.desktop.dialogs import DiffDialog
        self.assertIs(author_approval.approved,workflow_controller.approved)
        for operation in ('chapter_revision','material_import'):
            p=SimpleNamespace(window=None,core=Mock(),refresh_view=Mock(),refresh_recovery=Mock())
            from PySide6.QtWidgets import QWidget
            p.window=QWidget()
            p.window.status_text=Mock()
            preview={'handle':'synthetic-only','operation':operation,'target':'synthetic','diff':'synthetic diff'}
            with patch.object(DiffDialog,'exec',return_value=QDialog.Rejected):
                self.assertIsNone(author_approval.approved(p,preview))
            p.core.author_commit.assert_not_called()
            p.refresh_view.assert_not_called()
            with patch.object(DiffDialog,'exec',return_value=QDialog.Accepted):
                self.assertIs(author_approval.approved(p,preview),p.core.author_commit.return_value)
            p.core.author_commit.assert_called_once_with('synthetic-only',True)
            p.refresh_view.assert_called_once_with()
            self.assertEqual(p.refresh_recovery.call_count,1 if operation=='chapter_revision' else 0)
            p.window.deleteLater()

    def test_runtime_type_annotations_remain_resolvable(self):
        from typing import get_type_hints
        from novel_studio.desktop.provider_ui import ProviderDialog
        from novel_studio.api_service import ProviderRegistry
        self.assertIs(get_type_hints(ProviderDialog.__init__)['registry'],ProviderRegistry)
