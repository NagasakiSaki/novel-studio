"""Bounded v1 snapshot browsing; no permanent index or data migration.

Each cursor owns a streaming directory iterator, not a cached inventory. A page
reads at most 50 manifests; selecting a version verifies its bounded payloads.
Archive membership changes invalidate the cursor rather than silently skipping
or repeating records. Ordering is filesystem order within this browsing session.
"""
from pathlib import Path
import re
import secrets
import time

from .core import CoreError, SafeTree, admin, digest

FOLDERS = {'writer': 'Writer-Workspace/Context-Snapshot',
           'editor': 'Editor-Workspace/Manuscript-Snapshot'}


def folder(role):
    if role not in FOLDERS:
        raise CoreError('SNAPSHOT_INVALID', '历史副本角色无效。')
    return FOLDERS[role]


def marker(tree, root):
    with tree.directory(tuple(root.split('/'))) as (path, unused):
        info = path.stat()
        return info.st_dev, info.st_ino, info.st_mtime_ns


def stream(project, root):
    with SafeTree(project) as tree:
        yield from tree.children(root)


def close(core, cursor):
    session = getattr(core, '_snapshot_pages', {}).pop(cursor, None)
    if session:
        session['iterator'].close()


def page(core, role, cursor, limit):
    root = folder(role)
    if type(limit) is not int or not 1 <= limit <= 50:
        raise CoreError('SNAPSHOT_INVALID', '历史分页大小必须在 1 到 50 之间。')
    sessions = getattr(core, '_snapshot_pages', None)
    if sessions is None:
        core._snapshot_pages = sessions = {}
    now = time.monotonic()
    for token, old in list(sessions.items()):
        if old['expires'] < now:
            close(core, token)
    if cursor is not None and (not isinstance(cursor, str) or cursor not in sessions):
        raise CoreError('HISTORY_CHANGED', '历史分页已失效，请从第一页重新打开。')
    with SafeTree(core.project) as tree:
        current_marker = marker(tree, root)
        if cursor is None:
            if len(sessions) >= 8:
                close(core, next(iter(sessions)))
            session = {'role': role, 'marker': current_marker, 'iterator': stream(core.project, root)}
        else:
            session = sessions.pop(cursor)
        iterator = session['iterator']
        try:
            if session['role'] != role or session['marker'] != current_marker:
                raise CoreError('HISTORY_CHANGED', '历史目录已变化，请从第一页重新打开。')
            rows, exhausted = [], False
            # Bound inspected directory entries too, including unpublished snapshots.
            for unused in range(limit):
                try:
                    name, is_dir = next(iterator)
                except StopIteration:
                    exhausted = True
                    break
                if role == 'writer' and name == 'Author-Feedback' and is_dir:
                    continue
                if not is_dir or not re.fullmatch(r'[a-f0-9]{32}', name):
                    raise CoreError('SNAPSHOT_INVALID', '历史目录包含无效的副本标识。')
                path = root + '/' + name + '/SNAPSHOT.json'
                try:
                    manifest, revision = admin.snapshot_manifest(tree, path)
                except FileNotFoundError:
                    continue  # Manifest is published last; an interrupted copy is not a version.
                rows.append({'snapshot_id': name, 'role': role, 'revision': revision,
                             'files': manifest['files']})
            if marker(tree, root) != current_marker:
                raise CoreError('HISTORY_CHANGED', '历史目录已变化，请从第一页重新打开。')
            token = None
            if not exhausted:
                token = secrets.token_urlsafe(24)
                sessions[token] = {**session, 'expires': now + 600}
            else:
                iterator.close()
            return {'entries': rows, 'cursor': token, 'order': 'session filesystem order'}
        except BaseException:
            iterator.close()
            raise


def document(core, role, snapshot_id, name, revision):
    root = folder(role)
    if (not isinstance(snapshot_id, str) or not re.fullmatch(r'[a-f0-9]{32}', snapshot_id)
            or not isinstance(revision, str) or not re.fullmatch(r'[a-f0-9]{64}', revision)):
        raise CoreError('SNAPSHOT_INVALID', '历史副本标识或版本无效。')
    path = root + '/' + snapshot_id + '/SNAPSHOT.json'
    with SafeTree(core.project) as tree:
        manifest, actual = admin.snapshot_manifest(tree, path)
        if actual != revision or not isinstance(name, str) or name not in manifest['files']:
            raise CoreError('HISTORY_CHANGED', '历史副本版本或所选文件不匹配。')
        payload = admin.snapshot_payloads(tree, path, manifest)[name]
        # Recheck the manifest after content reads, matching normal revision checks.
        if digest(tree.read(path)) != revision:
            raise CoreError('HISTORY_CHANGED', '历史副本清单已变化。')
    return {'snapshot_id': snapshot_id, 'name': name, 'revision': digest(payload),
            'manifest_revision': revision, 'text': payload.decode('utf-8')}
