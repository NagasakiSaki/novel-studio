"""Opt-in local benchmark. Synthetic files only; never sends model requests."""
import argparse
from collections import Counter
import json
import math
import os
from pathlib import Path
import platform
import statistics
import sys
import tempfile
import time
import tracemalloc
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests')]
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from novel_studio.core import Core, SafeTree, admin, digest
from novel_studio.material_search import search
from novel_studio.exporting import ExportService, DEFAULTS
from novel_studio.prompting import assemble


def fixture(parent, count):
    core = Core.create(parent, 'synthetic-' + str(count), ROOT)
    chapters = {}
    with SafeTree(core.project) as tree:
        for n in range(1, count + 1):
            relative = f'Writer-Workspace/Drafts/chapter-{n:04}.md'
            raw = (f'# 合成第{n}章\n' + ('林舟走进钟楼，周岚交出旧齿轮。街道在雨中安静下来。\n' * 120)).encode()
            tree.create(relative, raw)
            ref = {'document_id': digest(relative.encode())[:32], 'revision': digest(raw)}
            cid = f'{n:032x}'
            row = {'chapter_id': cid, 'ordinal': n, 'volume': 1, 'number': n,
                   'volume_title': '合成卷', 'title': f'合成第{n}章', 'status': 'DRAFT_READY',
                   'draft': ref, 'canonical': None, 'review': None, 'next_direction': {}}
            # Keep every existing SafeTree 1000-file boundary. 1000 chapters
            # have 1000 drafts and 500 formal versions, plus actual settings.
            if n <= count // 2:
                formal = f'Canonical/Final-Manuscript/chapter-{n:04}.md'
                tree.create(formal, raw)
                row['canonical'] = {'document_id': digest(formal.encode())[:32], 'revision': digest(raw)}
            chapters[cid] = row
        for n in range(20):
            tree.create(f'Canonical/Canon-Characters/person-{n:02}.md', ('# 合成人物\n林舟的经历和关系。' * 30).encode())
            tree.create(f'Canonical/Canon-Worldbuilding/world-{n:02}.md', ('# 合成世界\n钟楼的制度和旧城。' * 30).encode())
    core.workflow.store.mutate('synthetic_profile', lambda s, t: s.update(chapters=chapters))
    # Actual retained large material, registered using the existing author path.
    from novel_studio.materials import Prepared
    from novel_studio.material_formats import parse
    source = parent / ('material-' + str(count) + '.md')
    source.write_text('# 常驻规则\n保留人物灰度。\n' + ''.join(f'\n## 地点{i}\n' + ('钟楼人物林舟周岚世界设定档案。\n' * 100) for i in range(40)), encoding='utf-8')
    raw = source.read_bytes()
    parsed = parse(raw, '.md')
    item = Prepared(source, raw, parsed, 'global_preset', '')
    preview = core.materials.preview(item, [parsed['sections'][0]['id']], 'retrieval', True)
    core.author_commit(preview['handle'], True)
    context = core.snapshot()
    draft = 'Writer-Workspace/Drafts/chapter-0001.md'
    for unused in range(8):
        review = admin.review_snapshot(core.project, draft, context['destination'] + '/SNAPSHOT.json', True)
    admin.ui_state(core.project, {'review': review['destination'] + '/SNAPSHOT.json'})
    return core


def hashes(folder):
    return {p.relative_to(folder).as_posix(): digest(p.read_bytes()) for p in folder.rglob('*') if p.is_file()}


def measure(operation, samples):
    times, counts = [], []
    original_read, original_files, original_json = SafeTree.read, SafeTree.files, json.loads
    for unused in range(samples):
        c = Counter()
        def read(tree, name):
            c['reads'] += 1
            result = original_read(tree, name)
            c['bytes'] += len(result)
            return result
        def files(tree, *args, **kwargs):
            c['scans'] += 1
            result = original_files(tree, *args, **kwargs)
            c['enumerated_files'] += len(result)
            return result
        def loads(*args, **kwargs):
            c['json_parses'] += 1
            return original_json(*args, **kwargs)
        with patch.object(SafeTree, 'read', read), patch.object(SafeTree, 'files', files), patch.object(json, 'loads', loads):
            start = time.perf_counter()
            operation()
            times.append(time.perf_counter() - start)
        counts.append(dict(c))
    # Allocation tracing is separate: never compare traced and untraced times.
    tracemalloc.start()
    operation()
    unused_current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {'first_seconds': times[0], 'warm_p50_seconds': statistics.median(times[1:]),
            'warm_p95_seconds': sorted(times[1:])[math.ceil(.95 * (samples - 1)) - 1],
            'seconds': times, 'file_counters': counts, 'python_traced_peak_bytes_separate_run': peak}


def run(output, sizes, samples, only=None):
    from PySide6.QtWidgets import QApplication
    from novel_studio.desktop.window import Workbench
    from novel_studio.desktop.presenter import Presenter
    from ui2_fixtures import LocalOnlyCLI
    app = QApplication.instance() or QApplication([])
    report = {'environment': {'os': platform.platform(), 'python': sys.version, 'executable': sys.executable,
              'samples': samples, 'cold_definition': 'open_project constructs fresh Core each time; other first calls are operation-first, not OS-cold; filesystem cache not flushed',
              'p95_method': 'nearest rank, small sample descriptive only', 'qt': 'offscreen',
              'memory_scope': 'Python tracemalloc separate warm run; not process RSS or native Qt memory'},
              'scales': {}, 'model_calls': 0}
    with tempfile.TemporaryDirectory(prefix='b06-synthetic-') as temp:
        parent = Path(temp)
        # Prevent all environment/profile discovery outside synthetic state.
        os.environ['LOCALAPPDATA'] = str(parent / 'local')
        os.environ['APPDATA'] = str(parent / 'roaming')
        for count in sizes:
            core = fixture(parent, count)
            before = hashes(core.project)
            window = Workbench()
            presenter = Presenter(window, parent / ('state-' + str(count)), LocalOnlyCLI())
            target = digest(b'Writer-Workspace/Drafts/chapter-0001.md')[:32]
            def open_book():
                presenter.open_project(core.project)
            def history():
                page = core.snapshot_history(limit=3)
                if page['cursor']:
                    core.close_snapshot_history(page['cursor'])
            def export():
                service = ExportService(core)
                chosen = service.select('draft', [target])
                service.preview(chosen, {**DEFAULTS, 'title': '合成作品'}, 'txt')
            def material():
                record = core.materials.records()[0]
                cache = getattr(core, '_search_cache', None)
                return search(record, '钟楼林舟', cache=cache) if cache is not None else search(record, '钟楼林舟')
            def save():
                row = core.document(target)
                with SafeTree(core.project) as tree:
                    tree.publish(row['relative'], row['text'].encode(), row['revision'])
            operations = {'open_project': open_book, 'refresh_view': presenter.refresh_view,
                'catalog': core.documents, 'switch_chapter': lambda:core.document(target),
                'chapter_directory': core.workflow.chapters.catalog, 'history': history,
                'material_search': material, 'character_query': lambda:core.document(digest(b'Canonical/Canon-Characters/person-00.md')[:32]),
                'world_query': lambda:core.document(digest(b'Canonical/Canon-Worldbuilding/world-00.md')[:32]),
                'writer_context': lambda:assemble(core, 'writer', '钟楼林舟', source_document_id=target, api=True),
                'editor_context': lambda:assemble(core, 'editor', '钟楼林舟', api=True),
                'save_same_draft': save, 'export_one_chapter_preview': export}
            result = {}
            for name, op in operations.items():
                if only and name not in only:
                    continue
                print(count, name, flush=True)
                if hasattr(core, 'clear_read_caches'):
                    core.clear_read_caches()
                    if presenter.core is not None:
                        presenter.core.clear_read_caches()
                result[name] = measure(op, samples)
            # Do not invent a manuscript search feature to produce a benchmark.
            result['fullbook_search'] = {'status': 'NOT APPLICABLE', 'reason': 'Product only exposes material FTS, not manuscript-wide search; no new feature added'}
            result['data_unchanged'] = hashes(core.project) == before
            result['fixture'] = {'chapters': count, 'drafts': count, 'formal': count // 2, 'character_files': 20, 'world_files': 20, 'review_snapshots': 8}
            if hasattr(core, 'cache_diagnostics'):
                result['local_cache'] = core.cache_diagnostics()
            report['scales'][str(count)] = result
            window.close()
            window.deleteLater()
            app.processEvents()
            Path(output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--sizes', nargs='+', type=int, default=[100, 334, 500, 1000])
    parser.add_argument('--samples', type=int, default=6)
    parser.add_argument('--operations', nargs='+')
    args = parser.parse_args()
    run(args.output, args.sizes, args.samples, args.operations)
