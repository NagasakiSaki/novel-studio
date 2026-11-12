"""B06 correctness and file-work assertions; synthetic data, no model calls."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests')]
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from novel_studio.core import Core, CoreError, SafeTree, digest, admin
from novel_studio.material_search import search
from novel_studio.prompting import compile_prompt


class ReadPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='b06-read-')
        self.addCleanup(self.temp.cleanup)
        self.core = Core.create(Path(self.temp.name), 'synthetic', ROOT)
        with SafeTree(self.core.project) as tree:
            tree.create('Canonical/Canon-Worldbuilding/setting.md', b'world setting')
            for n in range(32):
                tree.create(f'Writer-Workspace/Drafts/{n}.md', f'# 合成{n}\n钟楼与人物。'.encode())
        self.rows = self.core.documents()
        self.row = next(r for r in self.rows if r['relative'].endswith('/0.md'))

    def test_known_chapter_read_has_one_payload_and_no_scan(self):
        reads = []
        original = SafeTree.read
        def read(tree, name):
            reads.append(name)
            return original(tree, name)
        with patch.object(SafeTree, 'files', side_effect=AssertionError('unnecessary fullbook scan')), patch.object(SafeTree, 'read', read):
            for unused in range(5):
                value = self.core.document(self.row['document_id'])
                self.assertIn('钟楼', value['text'])
        self.assertEqual(reads, [self.row['relative']] * 5)

    def test_external_edit_same_size_and_mtime_is_seen_then_delete_denied(self):
        path = self.core.project / self.row['relative']
        before = path.stat()
        raw = path.read_bytes().replace('钟楼'.encode(), '旧城'.encode())
        path.write_bytes(raw)
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        row = self.core.document(self.row['document_id'])
        self.assertEqual(row['revision'], digest(raw))
        self.assertIn('旧城', row['text'])
        path.unlink()
        with self.assertRaises((CoreError, FileNotFoundError)):
            self.core.document(self.row['document_id'])

    def test_role_membership_path_and_new_files_rechecked(self):
        with self.assertRaises(CoreError):
            self.core.document(self.row['document_id'], 'editor')
        with self.assertRaises(CoreError):
            self.core.document('../Canonical/secret')
        with SafeTree(self.core.project) as tree:
            tree.create('Writer-Workspace/Drafts/new.md', b'new')
        self.assertEqual(self.core.document(digest(b'Writer-Workspace/Drafts/new.md')[:32])['text'], 'new')
        fresh = Core(self.core.project)
        self.assertEqual(fresh.document(self.row['document_id'])['revision'], self.row['revision'])

    def test_cache_cannot_authorize_old_snapshot_or_mask_corruption(self):
        context = self.core.snapshot()
        rows = self.core.documents('writer')
        old = next(r for r in rows if r['area'] == 'writer_context')
        admin.ui_state(self.core.project, {'context': None})
        with self.assertRaises(CoreError):
            self.core.document(old['document_id'], 'writer')
        admin.ui_state(self.core.project, {'context': context['destination'] + '/SNAPSHOT.json'})
        (self.core.project / old['relative']).write_bytes(b'tampered')
        with self.assertRaises(ValueError):
            self.core.document(old['document_id'], 'writer')

    def test_measurement_reuse_content_invalidation_and_mutable_result_isolation(self):
        from novel_studio import text_metrics
        with patch.object(text_metrics, 'measure', wraps=text_metrics.measure) as measured:
            one = self.core.measure_text('钟楼人物。')
            one['count'] = -1
            self.assertGreater(self.core.measure_text('钟楼人物。')['count'], 0)
            self.core.measure_text('钟楼人物。', 'han_letters_digits_v1')
            self.core.measure_text('旧城人物。')
        self.assertEqual(measured.call_count, 3)
        stats = self.core.cache_diagnostics()['derived']
        self.assertGreater(stats['hits'], 0)

    def test_document_path_cache_is_bounded_and_misses_fall_back_safely(self):
        self.assertLessEqual(self.core.cache_diagnostics()['document_paths'], 4096)
        other = Core.create(Path(self.temp.name), 'other', ROOT)
        with self.assertRaises(CoreError):
            other.document(self.row['document_id'])
        self.core.clear_read_caches()
        # A selected result is promoted even if a huge catalogue evicted it.
        with patch.object(self.core, 'documents', return_value=self.rows) as scanned:
            self.core.document(self.row['document_id'])
            self.core.document(self.row['document_id'])
        self.assertEqual(scanned.call_count, 1)

    def test_catalog_file_limit_is_not_removed_by_caching(self):
        with SafeTree(self.core.project) as tree:
            with self.assertRaises(ValueError):
                tree.files('Writer-Workspace/Drafts', file_limit=10)

    def test_export_catalog_skips_context_and_counts_exact_checked_bytes(self):
        from collections import Counter
        from novel_studio.exporting import ExportService
        self.core.snapshot()
        reads = Counter()
        original = SafeTree.read
        def read(tree, name):
            self.assertNotIn('Context-Snapshot/', str(name))
            self.assertNotIn('Manuscript-Snapshot/', str(name))
            reads[str(name)] += 1
            return original(tree, name)
        with patch.object(SafeTree, 'read', read):
            rows = ExportService(self.core).catalog()
        for row in rows:
            self.assertEqual(reads[row['relative']], 1)
            self.assertGreater(row['word_count'], 0)
        with self.assertRaises(CoreError):
            self.core.documents('writer', _areas=('canonical',))

    def test_context_reuse_preserves_exact_source_and_prompt(self):
        from b06_profile import fixture
        from novel_studio.prompting import assemble
        core = fixture(Path(self.temp.name), 4)
        self.addCleanup(core.clear_read_caches)
        a, receipt_a = assemble(core, 'writer', '钟楼林舟', api=True)
        b, receipt_b = assemble(core, 'writer', '钟楼林舟', api=True)
        self.assertEqual((a.stable_text, a.dynamic_text, receipt_a), (b.stable_text, b.dynamic_text, receipt_b))
        from novel_studio.material_search import SearchCache
        normal = core._search_cache
        core._search_cache = SearchCache(max_entries=0)
        try:
            c, receipt_c = assemble(core, 'writer', '钟楼林舟', api=True)
        finally:
            core._search_cache.clear()
            core._search_cache = normal
        self.assertEqual((a.stable_text, a.dynamic_text, receipt_a), (c.stable_text, c.dynamic_text, receipt_c))

    def test_each_assembly_checks_snapshot_payloads_once_and_next_call_checks_again(self):
        from collections import Counter
        from b06_profile import fixture
        from novel_studio.prompting import assemble
        core = fixture(Path(self.temp.name), 4)
        self.addCleanup(core.clear_read_caches)
        core.documents()  # Chapter locator discovery is a separate operation.
        original = SafeTree.read
        for role in ('writer', 'editor', 'shared'):
            state = admin.ui_state(core.project)
            manifest = state['context' if role in {'writer', 'shared'} else 'review']
            names = json.loads((core.project / manifest).read_bytes())['files']
            for unused in range(2):
                reads = Counter()
                def read(tree, name):
                    reads[str(name).replace('\\', '/')] += 1
                    return original(tree, name)
                with patch.object(SafeTree, 'read', read):
                    if role == 'shared':
                        from novel_studio.workflow_requests import shared_context
                        shared_context(core, '钟楼林舟')
                    else:
                        assemble(core, role, '钟楼林舟', api=True)
                for name in names:
                    relative = (Path(manifest).parent / name).as_posix()
                    self.assertEqual(reads[relative], 1, relative)

    def test_call_local_context_cannot_cross_owner_or_role_or_skip_next_verification(self):
        from b06_profile import fixture
        from novel_studio.prompting import assemble
        core = fixture(Path(self.temp.name), 4)
        self.addCleanup(core.clear_read_caches)
        view = core._context_view('writer')
        with self.assertRaises(CoreError):
            core.materials.context('editor', '钟楼', _view=view)
        another = Core(core.project)
        self.addCleanup(another.clear_read_caches)
        with self.assertRaises(CoreError):
            another.materials.context('writer', '钟楼', _view=view)
        state = admin.ui_state(core.project)
        path = state['context']
        first = next(iter(json.loads((core.project / path).read_bytes())['files']))
        (core.project / Path(path).parent / first).write_bytes(b'tampered snapshot')
        with self.assertRaises(CoreError):
            assemble(core, 'writer', '钟楼', api=True)

    def test_concurrent_read_and_save_never_use_stale_body(self):
        from threading import Event
        import errno
        start = Event()
        initial = self.core.document(self.row['document_id'])['text']
        candidates = {initial, 'a' * 1000, 'b' * 1000}
        def read(unused):
            start.wait()
            successes = 0
            for unused in range(30):
                try:
                    row = self.core.document(self.row['document_id'])
                except ValueError:
                    continue  # Secure-read/CAS rejection is valid during replace.
                except OSError as error:
                    if error.errno not in {errno.EACCES, errno.ENOENT} and getattr(error, 'winerror', None) not in {32, 33}:
                        raise
                    continue
                self.assertEqual(digest(row['text'].encode()), row['revision'])
                self.assertIn(row['text'], candidates)
                successes += 1
            return successes
        def save():
            start.wait()
            successes = 0
            for n in range(10):
                try:
                    with SafeTree(self.core.project) as tree:
                        with tree.lock():
                            current = tree.read(self.row['relative'])
                            tree.publish(self.row['relative'], (('a' if n % 2 else 'b') * 1000).encode(), digest(current))
                    successes += 1
                except OSError as error:
                    if error.errno != errno.EACCES and getattr(error, 'winerror', None) not in {32, 33}:
                        raise
            return successes
        with ThreadPoolExecutor(max_workers=4) as pool:
            readers = [pool.submit(read, n) for n in range(3)]
            writer = pool.submit(save)
            start.set()
            self.assertGreater(sum(f.result() for f in readers), 0)
            self.assertGreater(writer.result(), 0)
        with SafeTree(self.core.project) as tree:
            tree.publish(self.row['relative'], b'changed', digest(tree.read(self.row['relative'])))
        self.assertEqual(self.core.document(self.row['document_id'])['text'], 'changed')

    def test_file_type_and_hardlink_checks_still_run_on_warm_read(self):
        original = self.core.project / self.row['relative']
        alias = original.with_name('alias.md')
        try:
            os.link(original, alias)
        except OSError:
            self.skipTest('OS does not permit a synthetic hard link')
        with self.assertRaises(ValueError):
            self.core.document(self.row['document_id'])

    def test_history_restore_and_snapshot_hashes_remain_fresh(self):
        context = self.core.snapshot()
        review = admin.review_snapshot(self.core.project, self.row['relative'], context['destination'] + '/SNAPSHOT.json', True)
        with SafeTree(self.core.project) as tree:
            tree.publish(self.row['relative'], b'new current', self.row['revision'])
        restored = self.core.restore_snapshot(review['snapshot_id'], 'Drafts/0.md', digest((self.core.project / review['destination'] / 'SNAPSHOT.json').read_bytes()))
        self.assertIn('钟楼', self.core.document(restored['document_id'])['text'])
        self.assertEqual(self.core.document(self.row['document_id'])['text'], 'new current')

    def test_parsed_json_is_content_keyed_and_deeply_copied(self):
        raw = b'{"chapters":{"a":{"title":"one"}}}'
        one = self.core.parse_json(raw)
        one['chapters']['a']['title'] = 'mutated'
        self.assertEqual(self.core.parse_json(raw)['chapters']['a']['title'], 'one')
        self.assertEqual(self.core.parse_json(raw.replace(b'one', b'two'))['chapters']['a']['title'], 'two')
        with self.assertRaises(ValueError):
            self.core.parse_json(b'{"chapters":')


class CacheTests(unittest.TestCase):
    def record(self, text='钟楼人物林舟。', identifier='a'):
        return {'material_id': identifier, 'text': text, 'sections': [{'id': 's', 'title': '钟楼', 'start': 0, 'end': len(text)}]}

    def test_fts_reuse_has_identical_results_and_content_sections_invalidate(self):
        from novel_studio.material_search import SearchCache
        cache = SearchCache()
        self.addCleanup(cache.clear)
        record = self.record()
        self.assertEqual(search(record, '林舟'), search(record, '林舟', cache=cache))
        self.assertEqual(search(record, '钟楼', cache=cache), search(record, '钟楼'))
        self.assertEqual(cache.stats()['misses'], 1)
        self.assertEqual(cache.stats()['hits'], 1)
        record['sections'][0]['level'] = 3  # Not an FTS/chunk input.
        self.assertEqual(search(record, '钟楼', cache=cache), search(record, '钟楼'))
        self.assertEqual(cache.stats()['misses'], 1)
        record['text'] = '旧城人物周岚。'
        self.assertEqual(search(record, '周岚', cache=cache), search(record, '周岚'))
        record['sections'][0]['title'] = '周岚'
        self.assertEqual(search(record, '周岚', cache=cache), search(record, '周岚'))
        self.assertEqual(cache.stats()['misses'], 3)

    def test_five_queries_build_one_index_and_preserve_every_result(self):
        import sqlite3
        from novel_studio.material_search import SearchCache
        record = self.record('钟楼人物林舟与周岚的档案。' * 100)
        queries = ['钟楼', '林舟', '周岚', '人物', '档案']
        with patch('sqlite3.connect', wraps=sqlite3.connect) as created:
            expected = [search(record, q) for q in queries]
        self.assertEqual(created.call_count, 5)
        cache = SearchCache()
        self.addCleanup(cache.clear)
        with patch('sqlite3.connect', wraps=sqlite3.connect) as created:
            actual = [search(record, q, cache=cache) for q in queries]
        self.assertEqual(actual, expected)
        self.assertEqual(created.call_count, 1)
        self.assertEqual(cache.stats()['hits'], 4)

    def test_lru_capacity_bypass_clear_and_thread_reuse(self):
        from novel_studio.material_search import SearchCache
        cache = SearchCache(max_entries=2, max_bytes=300000)
        self.addCleanup(cache.clear)
        for identifier in 'abc':
            search(self.record(identifier=identifier), '钟楼', cache=cache)
        self.assertEqual(cache.stats()['entries'], 2)
        self.assertEqual(cache.stats()['evictions'], 1)
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(lambda _:search(self.record(identifier='c'), '钟楼', cache=cache), range(15)))
        self.assertTrue(all(r == results[0] for r in results))
        tiny = SearchCache(max_bytes=1)
        self.addCleanup(tiny.clear)
        self.assertEqual(search(self.record(), '钟楼', cache=tiny), search(self.record(), '钟楼'))
        self.assertEqual(tiny.stats()['entries'], 0)
        self.assertGreater(tiny.stats()['bypasses'], 0)
        cache.clear()
        self.assertEqual(cache.stats()['entries'], 0)

    def test_unusable_memory_index_rebuilds_without_touching_source(self):
        from novel_studio.material_search import SearchCache
        cache = SearchCache()
        self.addCleanup(cache.clear)
        record = self.record()
        expected = search(record, '钟楼', cache=cache)
        connection = next(iter(cache._entries.values()))[0]
        connection.close()  # Synthetic loss of disposable derived state.
        self.assertEqual(search(record, '钟楼', cache=cache), expected)
        self.assertEqual(cache.stats()['rebuilds'], 1)

    def test_repeated_search_memory_and_index_count_are_bounded(self):
        import gc
        import tracemalloc
        from novel_studio.material_search import SearchCache
        cache = SearchCache(max_entries=2)
        self.addCleanup(cache.clear)
        record = self.record('钟楼人物林舟。' * 100)
        search(record, '钟楼', cache=cache)
        tracemalloc.start()
        try:
            for unused in range(200):
                search(record, '钟楼', cache=cache)
            gc.collect()
            before = tracemalloc.get_traced_memory()[0]
            for unused in range(1000):
                search(record, '钟楼', cache=cache)
            gc.collect()
            after = tracemalloc.get_traced_memory()[0]
            self.assertLess(after - before, 256 * 1024)
        finally:
            tracemalloc.stop()
        self.assertEqual(cache.stats()['entries'], 1)
        self.assertLessEqual(cache.stats()['accounted_bytes'], cache.max_bytes)

    def test_derived_lru_has_bounded_weight_and_copies_values(self):
        from novel_studio.read_cache import DerivedCache
        cache = DerivedCache(max_entries=2, max_bytes=30)
        for k in 'abc':
            cache.get(k, lambda:{'count': 1}, weight=10)
        self.assertEqual(cache.stats()['entries'], 2)
        self.assertEqual(cache.stats()['evictions'], 1)
        cache.get('x', lambda:[], weight=31)
        self.assertGreater(cache.stats()['bypasses'], 0)

    def test_prompt_prefix_and_model_settings_are_separate_from_live_content(self):
        common = dict(role='writer', project_id='book-a', always='批准规则', current={'text': '当前稿'})
        one = compile_prompt(**common, configuration={'model_id': 'one', 'layout': 'tab-a'})
        ui = compile_prompt(**common, configuration={'model_id': 'one', 'layout': 'tab-b'})
        model = compile_prompt(**common, configuration={'model_id': 'two'})
        self.assertEqual(one.fingerprints, ui.fingerprints)
        self.assertEqual(one.stable_text, model.stable_text)
        self.assertNotEqual(one.fingerprints['configuration'], model.fingerprints['configuration'])
        current = compile_prompt(**{**common, 'current': {'text': '新稿'}})
        self.assertEqual(one.stable_text, current.stable_text)
        self.assertNotEqual(one.dynamic_text, current.dynamic_text)
        for change in ({'always':'新规则'}, {'project_id':'book-b'}, {'role':'editor'}):
            self.assertNotEqual(one.stable_text, compile_prompt(**{**common, **change}).stable_text)


try:
    from PySide6.QtWidgets import QApplication
    QT = True
except ImportError:
    QT = False


@unittest.skipUnless(QT, 'Qt environment unavailable')
class RefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_refresh_reuses_one_catalog_and_switch_does_not_block_on_book_scan(self):
        from novel_studio.desktop.window import Workbench
        from novel_studio.desktop.presenter import Presenter
        from ui2_fixtures import LocalOnlyCLI
        with tempfile.TemporaryDirectory(prefix='b06-ui-') as temp:
            core = Core.create(Path(temp), 'synthetic', ROOT)
            with SafeTree(core.project) as tree:
                tree.create('Writer-Workspace/Drafts/one.md', '合成正文'.encode())
            window = Workbench()
            p = Presenter(window, Path(temp) / 'state', LocalOnlyCLI())
            self.addCleanup(window.deleteLater)
            p.open_project(core.project)
            with patch.object(p.core, 'documents', wraps=p.core.documents) as catalog:
                p.refresh_view()
            self.assertEqual(catalog.call_count, 1)
            with patch.object(SafeTree, 'files', side_effect=AssertionError('chapter switch scanned book')):
                p.load_document(digest(b'Writer-Workspace/Drafts/one.md')[:32])
            self.assertEqual(window.editor.toPlainText(), '合成正文')
            with patch('novel_studio.desktop.presenter.InformationDialog') as dialog:
                p.action('usage', {})
            text = dialog.call_args.args[1]
            self.assertIn('本地计算缓存（不是模型缓存）', text)
            self.assertIn('尚无法确认统计分母', text)
            old_core = p.core
            p.open_project(core.project)
            self.assertEqual(old_core.cache_diagnostics()['derived']['entries'], 0)

    def pump(self, condition, timeout=10):
        import time
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertTrue(condition(), 'background projection did not complete')

    def test_background_load_heartbeat_cancel_retry_and_project_binding(self):
        from threading import Event, get_ident
        from PySide6.QtCore import QTimer
        from novel_studio.desktop.window import Workbench
        from novel_studio.desktop.presenter import Presenter
        from novel_studio.host import ModelService
        from ui2_fixtures import LocalOnlyCLI
        with tempfile.TemporaryDirectory(prefix='b06-background-') as temp:
            core = Core.create(Path(temp), 'synthetic', ROOT)
            other = Core.create(Path(temp), 'other', ROOT)
            with SafeTree(core.project) as tree:
                tree.create('Writer-Workspace/Drafts/one.md', b'synthetic body')
            window = Workbench()
            p = Presenter(window, Path(temp) / 'state', LocalOnlyCLI(), background_load=True)
            self.addCleanup(window.deleteLater)
            release, started = Event(), Event()
            threads, beats = [], []
            original = Core.documents
            def gated(instance, role='author', **kwargs):
                if instance.project == core.project:
                    threads.append(get_ident())
                    started.set()
                    while not release.wait(.01):
                        if kwargs.get('_cancel') is not None and kwargs['_cancel'].is_set():
                            return original(instance, role, **kwargs)
                return original(instance, role, **kwargs)
            timer = QTimer()
            timer.setInterval(5)
            timer.timeout.connect(lambda:beats.append(1))
            timer.start()
            try:
                with patch.object(Core, 'documents', gated), patch.object(p, 'show_error') as errors:
                    p.open_project(core.project)
                    self.pump(lambda:started.is_set() and len(beats) >= 3)
                    self.assertNotEqual(threads[0], get_ident())
                    self.assertEqual(window.tree.topLevelItemCount(), 0)
                    with self.assertRaises(CoreError):
                        p.open_project(other.project)
                    p.cancel_load_button.click()
                    self.pump(lambda:p.job is None)
                    self.assertEqual(window.tree.topLevelItemCount(), 0)
                    self.assertFalse(errors.called)
                    release.set()
                    p.open_project(core.project)
                    self.pump(lambda:p.job is None)
                    self.assertGreater(window.tree.topLevelItemCount(), 0)
                    release.clear()
                    started.clear()
                    p.open_project(core.project)
                    self.pump(started.is_set)
                    # Fault injection: even an external replacement of the Core
                    # cannot apply the prior work's data to a different book.
                    ModelService(other, LocalOnlyCLI())
                    other.models.attach_api(p.providers)
                    p.core = other
                    release.set()
                    self.pump(lambda:p.job is None)
                    self.assertEqual(window.tree.topLevelItemCount(), 0)
                    self.assertIsNone(p._view_core)
            finally:
                release.set()
                timer.stop()
                if p.job is not None:
                    self.pump(lambda:p.job is None)

    def test_background_failure_reports_error_without_partial_success(self):
        from novel_studio.desktop.window import Workbench
        from novel_studio.desktop.presenter import Presenter
        from ui2_fixtures import LocalOnlyCLI
        with tempfile.TemporaryDirectory(prefix='b06-background-error-') as temp:
            core = Core.create(Path(temp), 'synthetic', ROOT)
            window = Workbench()
            p = Presenter(window, Path(temp) / 'state', LocalOnlyCLI(), background_load=True)
            self.addCleanup(window.deleteLater)
            with patch.object(Core, 'documents', side_effect=OSError('synthetic read fault')), patch.object(p, 'show_error') as errors:
                p.open_project(core.project)
                self.pump(lambda:p.job is None)
            self.assertEqual(errors.call_count, 1)
            self.assertEqual(window.tree.topLevelItemCount(), 0)
            with patch.object(p, 'refresh_view', side_effect=ValueError('synthetic projection fault')), patch.object(p, 'show_error') as errors:
                p.open_project(core.project)
                self.pump(lambda:p.job is None)
            self.assertEqual(errors.call_count, 1)
            self.assertNotEqual(window.run_label.text(), '就绪')

    def test_job_completion_coalesces_callback_and_final_refresh(self):
        from novel_studio.desktop.window import Workbench
        from novel_studio.desktop.presenter import Presenter
        from ui2_fixtures import LocalOnlyCLI
        with tempfile.TemporaryDirectory(prefix='b06-refresh-coalesce-') as temp:
            core = Core.create(Path(temp), 'synthetic', ROOT)
            window = Workbench()
            p = Presenter(window, Path(temp) / 'state', LocalOnlyCLI())
            self.addCleanup(window.deleteLater)
            p.open_project(core.project)
            with patch.object(p.core, 'documents', wraps=p.core.documents) as catalog:
                p.start_job(lambda progress:None, lambda value:p.refresh_view(), 'synthetic local task')
                self.pump(lambda:p.job is None)
            self.assertEqual(catalog.call_count, 1)


if __name__ == '__main__':
    unittest.main()
