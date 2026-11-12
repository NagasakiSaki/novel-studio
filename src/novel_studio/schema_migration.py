"""Explicit metadata-only author migration/rollback, immutable full-byte backups."""
import difflib
import json
import secrets
import time
import uuid
from .core import CoreError, SafeTree, admin, digest
from .workflow_store import BASE

TARGET = BASE + '/state.json'
BACKUPS = 'Backups/Schema-Migrations'


def preview(core, restore=None):
    with core.mutex:
        core.require_idle()
        with SafeTree(core.project) as tree:
            before = tree.read(TARGET)
            state = core.workflow.store.decode(before)
            if restore:
                if not isinstance(restore, str) or len(restore) != 32 or any(c not in '0123456789abcdef' for c in restore):
                    raise CoreError('MIGRATION_INVALID', '迁移记录无效。')
                meta = json.loads(tree.read(BACKUPS + '/' + restore + '/receipt.json'))
                after = tree.read(BACKUPS + '/' + restore + '/before.json')
                if digest(before) != meta['after_sha256'] or digest(after) != meta['before_sha256']:
                    raise CoreError('REVISION_CONFLICT', '迁移后已有新创作记录或备份校验不符；不能直接恢复并覆盖新进度。')
            else:
                if state.get('product_schema', 1) != 1:
                    raise CoreError('MIGRATION_NOT_NEEDED', '当前记录已是受支持的新格式，无需迁移。')
                original = json.loads(before)
                original['product_schema'] = 2
                after = admin.encode(original)
            plan = {'target': TARGET, 'before_sha256': digest(before), 'after_sha256': digest(after),
                'payload': after, 'operation': 'restore' if restore else 'migrate'}
            handle = secrets.token_urlsafe(32)
            core._approvals[handle] = (time.monotonic() + 300, plan, ('workflow_schema', None))
            diff = ''.join(difflib.unified_diff(before.decode('utf-8').splitlines(True), after.decode('utf-8').splitlines(True), fromfile='当前创作记录', tofile='恢复后的原始记录' if restore else '明确升级后的记录'))
            return {'handle': handle, 'target': TARGET, 'diff': diff, 'operation': plan['operation']}


def history(core):
    with SafeTree(core.project) as tree:
        return [name.split('/')[-2] for name in tree.files(BACKUPS) if name.endswith('/receipt.json')] if (core.project / BACKUPS).exists() else []


def commit(core, plan):
    with SafeTree(core.project) as tree:
        with tree.lock():
            current = tree.read(TARGET)
            if digest(current) != plan['before_sha256'] or digest(plan['payload']) != plan['after_sha256']:
                raise CoreError('REVISION_CONFLICT', '预览后的创作记录已经变化，未覆盖。')
            core.workflow.store.decode(plan['payload'])
            transaction = uuid.uuid4().hex
            folder = BACKUPS + '/' + transaction
            tree.mkdir(folder)
            tree.create(folder + '/before.json', current)
            tree.create(folder + '/after.json', plan['payload'])
            tree.create(folder + '/receipt.json', admin.encode({k: v for k, v in plan.items() if k != 'payload'}))
            if digest(tree.read(folder + '/before.json')) != plan['before_sha256']:
                raise CoreError('MIGRATION_INVALID', '原记录备份校验失败，未更新。')
            tree.publish(TARGET, plan['payload'], plan['before_sha256'])
            return {'transaction_id': transaction, 'target': TARGET, 'operation': plan['operation']}
