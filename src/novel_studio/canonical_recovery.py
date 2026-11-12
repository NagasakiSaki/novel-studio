"""Author-only repair of approved, published chapter revisions.

All mutations: Core.mutex -> SafeTree -> EXCL project lock -> locked helpers.
Diagnosis is read-only. No recovery path publishes Canonical or calls a model.
The sidecar does not change existing project, Workflow or transaction formats.
"""
from contextlib import contextmanager
import copy
import json
from pathlib import Path
import re
import secrets
import shutil
import time
import uuid

from .core import CoreError, SafeTree, admin, digest
from .workflow_store import BASE as WORKFLOW, WorkflowStore, identifier

BASE = WORKFLOW + '/Canonical-Recovery'
FORMAT = 'novel-studio-canonical-recovery-v1'
MIB = 1024 * 1024
SLOTS = 16
RESERVE = 256 * MIB
WARNING = 4 * 1024 * MIB
HARD = 8 * 1024 * MIB
META_LIMIT = MIB
CONTROL_LIMIT = 256 * 1024
MAX_FILES = 80
STATE = WORKFLOW + '/state.json'
SHA = re.compile(r'[a-f0-9]{64}')
FILE = re.compile(r'\.ns-([a-f0-9]{32})-(PLAN|INTENT|ASSOCIATION|REGISTERED|DONE(?:-[a-f0-9]{32})?|CANCELLED|AUTH-[a-f0-9]{64})\.json')


def fail(message, code='RECOVERY_INVALID'):
    raise CoreError(code, message)


def sha(value):
    if not isinstance(value, str) or not SHA.fullmatch(value):
        fail('恢复记录中的摘要无效。')
    return value


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            fail('恢复记录存在重复字段。')
        result[key] = value
    return result


def decode(raw):
    result = json.loads(raw, object_pairs_hook=unique)
    if not isinstance(result, dict):
        fail('恢复记录必须是对象。')
    return result


def checkpoint(name):
    """Named deterministic fault-injection boundary; no production side effects."""


class CanonicalRecovery:
    def __init__(self, core):
        self.core = core
        self.store = WorkflowStore(core)
        self._control_hashes = {}

    @contextmanager
    def _write(self):
        with self.core.mutex:
            self.core.require_idle()
            with SafeTree(self.core.project) as tree:
                try:
                    with tree.lock():
                        yield tree
                except FileExistsError as error:
                    raise CoreError('BUSY', '另一窗口正在写入本作品，请稍后重试。') from error

    def _header(self, value, keys):
        if (set(value) != set(keys) | {'format', 'project_id'} or value.get('format') != FORMAT
                or value.get('project_id') != self.core.project_id):
            fail('恢复记录格式或所属作品不匹配。')
        return value

    def _record(self, **values):
        return {'format': FORMAT, 'project_id': self.core.project_id, **values}

    def _controls(self, tree):
        p_raw, q_raw = (tree.maybe_read(BASE + '/' + name + '.json') for name in ('Pending', 'Quota'))
        p = decode(p_raw) if p_raw else self._record(generation=0, entries={})
        q = decode(q_raw) if q_raw else self._record(generation=0, entries={}, used=0)
        self._header(p, {'generation', 'entries'})
        self._header(q, {'generation', 'entries', 'used'})
        for raw, ledger in ((p_raw, p), (q_raw, q)):
            if raw and len(raw) > CONTROL_LIMIT:
                fail('恢复登记超过容量限制。')
            if type(ledger['generation']) is not int or ledger['generation'] < 0 or not isinstance(ledger['entries'], dict) or len(ledger['entries']) > SLOTS:
                fail('恢复登记数量或代数无效。')
            slots = set()
            for txn, entry in ledger['entries'].items():
                identifier(txn)
                if not isinstance(entry, dict):
                    fail('恢复登记条目无效。')
                slot = entry.get('slot')
                if type(slot) is not int or not 0 <= slot < SLOTS or slot in slots:
                    fail('恢复槽位重复或无效。')
                slots.add(slot)
                sha(entry.get('intent_sha'))
                if entry.get('archive_sha') is not None:
                    sha(entry['archive_sha'])
                expected = {'slot', 'intent_sha', 'archive_sha'}
                if ledger is q:
                    expected |= {'chapter_id', 'target', 'before', 'context_sha', 'history_new'}
                    identifier(entry.get('chapter_id'))
                    self._target(entry.get('target'))
                    self._reference(entry.get('before'))
                    sha(entry.get('context_sha'))
                    if not isinstance(entry.get('history_new'), dict) or len(entry['history_new']) > 5:
                        fail('恢复历史备份计费无效。')
                    for key, size in entry['history_new'].items():
                        sha(key)
                        if type(size) is not int or not 0 < size <= 32 * MIB:
                            fail('恢复历史备份体积无效。')
                if set(entry) != expected:
                    fail('恢复登记包含未知字段。')
        if type(q['used']) is not int or not 0 <= q['used'] <= HARD:
            fail('恢复容量台账无效，未继续发布正式修订。')
        for txn, entry in p['entries'].items():
            other = q['entries'].get(txn)
            if other is None or any(entry[k] != other[k] for k in ('slot', 'intent_sha')):
                fail('Pending 与容量预留不一致，保留原记录。')
            if other['archive_sha'] and entry['archive_sha'] != other['archive_sha']:
                fail('存档登记不一致。')
        self._control_hashes = {'Pending': digest(p_raw) if p_raw is not None else None,
                                'Quota': digest(q_raw) if q_raw is not None else None}
        return p, q

    def _ledger(self, tree, name, value):
        path = BASE + '/' + name + '.json'
        old = tree.maybe_read(path)
        expected = self._control_hashes[name]
        if (digest(old) if old is not None else None) != expected:
            fail('恢复登记已被其他操作修改，未覆盖。', 'REVISION_CONFLICT')
        value['generation'] += 1
        payload = admin.encode(value)
        if len(payload) > CONTROL_LIMIT:
            fail('恢复登记超过容量限制。')
        tree.publish(path, payload, expected)
        self._control_hashes[name] = digest(payload)

    @staticmethod
    def _target(path):
        if not isinstance(path, str):
            fail('正式目标路径无效。')
        parts = admin.relative_parts(path)
        admin.canonical_target(path)
        if Path(*parts).as_posix() != path or parts[:2] != ('Canonical', 'Final-Manuscript'):
            fail('恢复目标超出正式章节范围。')
        return path

    @staticmethod
    def _reference(ref):
        if not isinstance(ref, dict) or not {'document_id', 'revision'} <= set(ref):
            fail('章节引用无效。')
        identifier(ref['document_id'])
        sha(ref['revision'])

    def _files(self, tree, folder):
        result = {}
        try:
            for name, directory in tree.children(folder):
                if name == 'SEALED.json' and folder.startswith(BASE + '/Archive/') and not directory:
                    continue
                if directory or len(result) >= MAX_FILES or not FILE.fullmatch(name):
                    fail('恢复槽位有未知文件或超过数量限制。')
                result[name] = None
        except FileNotFoundError:
            pass
        return result

    @staticmethod
    def _slot(slot):
        return BASE + '/Staging/' + f'{slot:02d}'

    @staticmethod
    def _path(folder, txn, kind):
        identifier(txn)
        return folder + '/.ns-' + txn + '-' + kind + '.json'

    def _put(self, tree, folder, txn, kind, value):
        payload = admin.encode(value)
        files = self._files(tree, folder)
        name = self._path(folder, txn, kind)
        if kind != 'PLAN':
            size = sum(len(tree.read(folder + '/' + f)) for f in files if '-PLAN.json' not in f)
            old = tree.maybe_read(name)
            if size + (0 if old else len(payload)) > META_LIMIT:
                fail('恢复审计预算已满，保留全部证据。', 'RECOVERY_CAPACITY')
        if len(payload) > (32 * MIB if kind == 'PLAN' else CONTROL_LIMIT) or len(files) >= MAX_FILES and Path(name).name not in files:
            fail('恢复记录超过数量或大小限制。', 'RECOVERY_CAPACITY')
        old = tree.maybe_read(name)
        if old is None:
            tree.create(name, payload)
        elif old != payload:
            fail('不可覆盖已有恢复凭证。')
        if tree.read(name) != payload:
            fail('恢复记录回读不一致。')
        return digest(payload)

    def _history(self, tree, raw, txn, quota):
        key = digest(raw)
        folder = WORKFLOW + '/History'
        path = folder + '/' + key + '.json'
        old = tree.maybe_read(path)
        if old is not None:
            if digest(old) != key:
                fail('Workflow 历史备份损坏。')
            return key
        charge = BASE + '/Charges/' + key + '.json'
        charge_raw = tree.maybe_read(charge)
        new_charge = None
        if charge_raw:
            receipt = self._header(decode(charge_raw), {'transaction_id', 'sha256', 'bytes'})
            identifier(receipt['transaction_id'])
            if receipt['sha256'] != key or receipt['bytes'] != len(raw):
                fail('历史备份容量凭证不一致。')
        else:
            owner = next((t for t, e in quota['entries'].items() if key in e['history_new']), txn)
            charged = quota['entries'][owner]['history_new']
            if key not in charged:
                if len(charged) >= 5:
                    fail('本次恢复已达不同前态备份上限。', 'RECOVERY_CAPACITY')
                charged[key] = len(raw)
                self._ledger(tree, 'Quota', quota)
            new_charge = self._record(transaction_id=owner, sha256=key, bytes=len(raw))
        tree.mkdir(folder)
        tree.create(path, raw)
        if digest(tree.read(path)) != key:
            fail('Workflow 原字节备份未能验证。')
        if new_charge is not None:
            tree.mkdir(BASE + '/Charges')
            tree.create(charge, admin.encode(new_charge))
        return key

    def _inventory(self, tree):
        found = {}
        try:
            for name, directory in tree.children(BASE + '/Staging'):
                if not directory or name not in {f'{i:02d}' for i in range(SLOTS)}:
                    fail('活动恢复槽位名称无效。')
                for filename in self._files(tree, BASE + '/Staging/' + name):
                    txn = FILE.fullmatch(filename)[1]
                    if txn in found and found[txn] != int(name):
                        fail('同一恢复事务占用多个槽位。')
                    found[txn] = int(name)
        except FileNotFoundError:
            pass
        return found

    def _load(self, tree, txn, slot, entry=None):
        identifier(txn)
        folder = self._slot(slot)
        if entry and entry.get('archive_sha'):
            folder = BASE + '/Archive/' + txn
            self._seal(tree, txn, entry['archive_sha'])
        if any(FILE.fullmatch(name)[1] != txn for name in self._files(tree, folder)):
            fail('恢复目录包含其他事务的文件。')
        raw = tree.read(self._path(folder, txn, 'INTENT'))
        intent = self._header(decode(raw), {'transaction_id', 'chapter_id', 'slot', 'plan_sha', 'before_state_sha', 'before_chapter', 'patch'})
        if len(raw) > CONTROL_LIMIT or intent['transaction_id'] != txn or intent['slot'] != slot:
            fail('恢复意图身份不一致。')
        identifier(intent['chapter_id'])
        if entry and digest(raw) != entry['intent_sha']:
            fail('批准意图摘要不一致。')
        plan_raw = tree.read(self._path(folder, txn, 'PLAN'))
        if digest(plan_raw) != sha(intent['plan_sha']):
            fail('获批计划摘要不一致。')
        plan = decode(plan_raw)
        if set(plan) != {'format', 'project', 'operation', 'source', 'target', 'before_sha256', 'after_sha256', 'diff', 'parent_transaction', 'approval_digest'}:
            fail('正式修订计划字段无效。')
        approved = {k: v for k, v in plan.items() if k != 'approval_digest'}
        if (plan['format'] != admin.FORMAT or plan['project'] != str(self.core.project)
                or plan['operation'] not in {'chapter_revision', 'restore'}
                or plan['operation'] == 'chapter_revision' and plan['parent_transaction'] is not None
                or digest(admin.encode(approved)) != sha(plan['approval_digest'])):
            fail('正式修订批准计划不匹配。')
        self._target(plan['target'])
        source_parts = admin.relative_parts(plan['source'])
        if plan['operation'] == 'restore':
            parent, before_body, after_body = self.receipt(tree, plan['parent_transaction'])
            if (plan['source'] != 'Backups/Transactions/' + parent['transaction_id'] + '/before.bin'
                    or plan['target'] != parent['target'] or plan['before_sha256'] != parent['after_sha256']
                    or plan['after_sha256'] != parent['before_sha256']):
                fail('恢复正文的父事务关联不一致。')
        elif (source_parts[:2] != ('Writer-Workspace', 'Drafts') or Path(*source_parts).as_posix() != plan['source']
                or Path(plan['source']).suffix.lower() not in {'.md', '.txt', '.json'}):
            fail('正式修订源不在本作品草稿区。')
        sha(plan['before_sha256']); sha(plan['after_sha256'])
        before = intent['before_chapter']
        if not isinstance(before, dict) or before.get('chapter_id') != intent['chapter_id']:
            fail('批准意图缺少精确章节。')
        self._reference(before.get('canonical'))
        document_id = digest(plan['target'].encode())[:32]
        if before['canonical']['document_id'] != document_id or before['canonical']['revision'] != plan['before_sha256']:
            fail('章节旧引用与获批目标不符。')
        if before.get('approval_transaction') is not None:
            identifier(before['approval_transaction'])
        if plan['operation'] == 'chapter_revision':
            for field in ('draft', 'review'):
                self._reference(before.get(field))
            if before['draft']['document_id'] != digest(plan['source'].encode())[:32]:
                fail('批准草稿身份不一致。')
        elif before.get('approval_transaction') != plan['parent_transaction']:
            fail('恢复父事务不属于当前章节版本。')
        patch = intent['patch']
        if (not isinstance(patch, dict) or set(patch) != {'canonical', 'approval_transaction', 'status', 'local_length'}
                or patch['canonical'] != {'document_id': document_id, 'revision': plan['after_sha256']}
                or patch['approval_transaction'] != txn or patch['status'] != 'ARCHIVED' or not isinstance(patch['local_length'], dict)):
            fail('恢复补丁超出已批准章节索引范围。')
        history = tree.read(WORKFLOW + '/History/' + sha(intent['before_state_sha']) + '.json')
        if digest(history) != intent['before_state_sha'] or self.store.decode(history)['chapters'].get(intent['chapter_id']) != before:
            fail('获批前态备份损坏或章节不符。')
        association = tree.maybe_read(self._path(folder, txn, 'ASSOCIATION'))
        if association is not None:
            expected = self._record(transaction_id=txn, chapter_id=intent['chapter_id'], intent_sha=digest(raw),
                                    purpose='metadata_association_only')
            if decode(association) != expected or len(association) > CONTROL_LIMIT:
                fail('旧记录人工关联凭证不一致。')
        return intent, plan, folder, digest(raw)

    def _authorization(self, tree, txn, intent, folder, intent_sha):
        auths = []
        for name in self._files(tree, folder):
            if '-AUTH-' not in name:
                continue
            raw = tree.read(folder + '/' + name)
            auth = self._header(decode(raw), {'transaction_id', 'chapter_id', 'intent_sha', 'state_sha', 'purpose'})
            if (auth['transaction_id'] != txn or auth['chapter_id'] != intent['chapter_id']
                    or auth['intent_sha'] != intent_sha or auth['purpose'] not in {'normal', 'recovery'}
                    or name != Path(self._path(folder, txn, 'AUTH-' + digest(admin.encode([auth['purpose'], sha(auth['state_sha'])])))).name):
                fail('索引恢复批准记录不匹配。')
            history = tree.read(WORKFLOW + '/History/' + auth['state_sha'] + '.json')
            if digest(history) != auth['state_sha'] or self.store.decode(history)['chapters'].get(intent['chapter_id']) != intent['before_chapter']:
                fail('索引恢复写入前备份不一致。')
            auths.append(auth)
        if len(auths) > 5:
            fail('恢复批准尝试超限。')
        return auths

    def _classify(self, tree, txn, slot, entry, pending, state):
        intent, plan, folder, intent_sha = self._load(tree, txn, slot, entry)
        cid = intent['chapter_id']
        matches = [c['chapter_id'] for c in state['chapters'].values() if (c.get('canonical') or {}).get('document_id') == intent['before_chapter']['canonical']['document_id']]
        if matches != [cid]:
            fail('正式文件不能唯一关联到获批章节。', 'RECOVERY_CONFLICT')
        current = state['chapters'][cid]
        new = {**intent['before_chapter'], **intent['patch']}
        actual = tree.maybe_read(plan['target'])
        actual_sha = digest(actual) if actual is not None else None
        txn_dir = 'Backups/Transactions/' + txn
        transactions = self._directory_exists(tree, txn_dir)
        if transactions:
            allowed = {'before.bin', 'after.bin', 'PREPARED.json', 'COMMITTED.json'}
            for index, (name, directory) in enumerate(tree.children(txn_dir)):
                if index >= 4 or directory or name not in allowed:
                    fail('正文事务文件数量或类型不符合要求。')
        registered_raw = tree.maybe_read(self._path(folder, txn, 'REGISTERED'))
        pending_entry = pending['entries'].get(txn)
        facts = {'transaction_id': txn, 'chapter_id': cid, 'volume': current['volume'], 'number': current['number'],
                 'title': current['title'], 'body_saved': actual_sha == plan['after_sha256'], 'approval_valid': True,
                 'index_updated': current == new, 'can_recover': False, 'can_close': False, 'slot': slot}
        facts['approval_kind'] = ('manual_association' if tree.maybe_read(self._path(folder, txn, 'ASSOCIATION')) is not None else 'publication')
        if not registered_raw:
            kind = 'PENDING_UNREGISTERED' if pending_entry else 'ORPHAN_UNREGISTERED'
            if transactions or actual_sha != plan['before_sha256'] or current != intent['before_chapter']:
                kind = 'CONFLICT'
            return {**facts, 'state': kind, 'can_close': kind != 'CONFLICT'}
        registered = self._header(decode(registered_raw), {'transaction_id', 'chapter_id', 'intent_sha', 'entry_sha'})
        original_entry = {'slot': slot, 'intent_sha': intent_sha, 'archive_sha': None}
        if registered != self._record(transaction_id=txn, chapter_id=cid, intent_sha=intent_sha, entry_sha=digest(admin.encode(original_entry))):
            fail('发布登记凭证无效。')
        if actual_sha not in {plan['before_sha256'], plan['after_sha256']}:
            return {**facts, 'state': 'CONFLICT'}
        committed_raw = tree.maybe_read(txn_dir + '/COMMITTED.json')
        if committed_raw is None:
            kind = 'CURRENT_BEFORE' if not transactions and actual_sha == plan['before_sha256'] and current == intent['before_chapter'] else 'PUBLISHED_NO_RECEIPT'
            return {**facts, 'state': kind, 'can_close': kind == 'CURRENT_BEFORE'}
        prepared = decode(tree.read(txn_dir + '/PREPARED.json'))
        committed = decode(committed_raw)
        if prepared != plan or committed != {**plan, 'transaction_id': txn, 'status': 'COMMITTED'}:
            fail('正文事务凭证与精确获批计划不符。')
        for name, expected in [('before.bin', plan['before_sha256']), ('after.bin', plan['after_sha256'])]:
            if digest(tree.read(txn_dir + '/' + name)) != expected:
                fail('正式修订备份摘要不匹配。')
        if actual_sha != plan['after_sha256']:
            return {**facts, 'state': 'CONFLICT'}
        auths = self._authorization(tree, txn, intent, folder, intent_sha)
        if current == intent['before_chapter']:
            if pending_entry is None:
                return {**facts, 'state': 'REGISTRY_MISSING', 'can_recover': True}
            return {**facts, 'state': 'INDEX_PENDING', 'can_recover': True}
        if current != new:
            # A different ID alone is not proof of a valid successor. Preserve
            # both known successors and unproven/ABA changes as a conflict.
            return {**facts, 'state': 'CONFLICT'}
        if not auths:
            fail('索引已变化但缺少本次索引写入批准。')
        done = self._record(transaction_id=txn, chapter_id=cid, intent_sha=intent_sha, chapter_sha=digest(admin.encode(new)))
        done_valid = self._done(tree, folder, txn, done)
        if not pending_entry and entry and not entry.get('archive_sha'):
            return {**facts, 'state': 'REGISTRY_MISSING'}
        if done_valid and entry and not entry.get('archive_sha') and not self._archive_consistent(tree, txn, folder):
            return {**facts, 'state': 'EVIDENCE_INVALID', 'reason': '完成存档与保留的源槽位不一致；未清理源记录，请人工核查存档。'}
        return {**facts, 'state': 'INDEX_DONE' if done_valid else 'INDEX_APPLIED_AUDIT_PENDING'}

    def _archive_consistent(self, tree, txn, source):
        """Inspect only this active operation's partial copy, never archive history."""
        target = BASE + '/Archive/' + txn
        if not self._directory_exists(tree, target):
            return True
        expected = self._files(tree, source)
        seal_raw = None
        try:
            for count, (name, directory) in enumerate(tree.children(target)):
                if directory or count > MAX_FILES:
                    return False
                if name == 'SEALED.json':
                    seal_raw = tree.read(target + '/' + name)
                elif name not in expected or tree.read(target + '/' + name) != tree.read(source + '/' + name):
                    return False
            if seal_raw is not None:
                sealed = self._seal(tree, txn, digest(seal_raw))
                if set(sealed['files']) != set(expected):
                    return False
            return True
        except (OSError, ValueError):
            return False

    @staticmethod
    def _directory_exists(tree, path):
        try:
            with tree.directory(admin.relative_parts(path)):
                return True
        except FileNotFoundError:
            return False

    def diagnose(self):
        with self.core.mutex, SafeTree(self.core.project) as tree:
            if tree.maybe_read('.novel-studio/.ns-author.lock') is not None:
                return {'operations': [], 'warnings': ['另一窗口正在写入，请稍后刷新。'], 'busy': True}
            controls_before = self._markers(tree)
            warnings, rows = [], []
            try:
                p, q = self._controls(tree)
            except (ValueError, OSError) as error:
                p, q = self._record(generation=0, entries={}), self._record(generation=0, entries={}, used=0)
                warnings.append('恢复登记或容量台账损坏，已停止新的正式修订；现有作品仍可读取。')
            try:
                inventory = self._inventory(tree)
            except (ValueError, OSError):
                inventory = {}
                warnings.append('恢复槽位包含无法确认的文件，请人工核查；未清理任何记录。')
            discovered = {**inventory, **{t: e['slot'] for t, e in q['entries'].items()}, **{t: e['slot'] for t, e in p['entries'].items()}}
            state = self.store.decode(tree.maybe_read(STATE))
            for txn, slot in discovered.items():
                entry = p['entries'].get(txn) or q['entries'].get(txn)
                try:
                    row = self._inspect(tree, txn, slot, entry, p, q, state)
                    if warnings:
                        row.update(can_recover=False, can_close=False)
                except (ValueError, OSError, KeyError, TypeError):
                    row = {'transaction_id': txn, 'state': 'EVIDENCE_INVALID', 'can_recover': False, 'can_close': False}
                rows.append(row)
            if q['used'] + RESERVE * len(q['entries']) >= WARNING:
                warnings.append('恢复记录接近容量预算；不会自动删除历史，达到上限将停止新的正式修订。')
            controls_after = self._markers(tree)
            busy = controls_before != controls_after or tree.maybe_read('.novel-studio/.ns-author.lock') is not None
            if busy:
                rows = []
                warnings.append('恢复记录正在变化，请刷新后再处理。')
            return {'operations': rows, 'warnings': warnings, 'busy': busy}

    @staticmethod
    def _markers(tree):
        markers = []
        for name in ('Pending', 'Quota'):
            try:
                raw = tree.maybe_read(BASE + '/' + name + '.json')
                markers.append(digest(raw) if raw is not None else None)
            except (OSError, ValueError):
                markers.append('UNREADABLE')
        return markers

    def _authorize(self, tree, txn, intent, folder, intent_sha, raw, purpose, quota):
        state_sha = digest(raw)
        auths = [n for n in self._files(tree, folder) if '-AUTH-' in n]
        name = 'AUTH-' + digest(admin.encode([purpose, state_sha]))
        if purpose == 'recovery' and len(auths) >= 5 and Path(self._path(folder, txn, name)).name not in auths:
            fail('已达四次不同前态恢复尝试上限。', 'RECOVERY_CAPACITY')
        self._history(tree, raw, txn, quota)
        existing = tree.maybe_read(self._path(folder, txn, name))
        if existing is None:
            self._put(tree, folder, txn, name, self._record(transaction_id=txn, chapter_id=intent['chapter_id'],
                intent_sha=intent_sha, state_sha=state_sha, purpose=purpose))

    def submit(self, envelope, cid):
        identifier(cid)
        with self._write() as tree:
            raw = tree.read(STATE)
            state = self.store.decode(raw)
            chapter = state['chapters'][cid]
            if digest(admin.encode(chapter)) != envelope['chapter_sha']:
                fail('批准后的章节上下文已变化，请重新预览。', 'REVISION_CONFLICT')
            plan = envelope['plan']
            self._target(plan['target'])
            associated = envelope.get('associated_transaction')
            if plan['operation'] == 'restore' or associated:
                if decode(tree.read('.novel-studio/project.json')) != self.core.metadata:
                    fail('作品身份发生变化，未执行恢复。', 'RECOVERY_CONFLICT')
            if associated:
                receipt, before_body, after_body = self.receipt(tree, associated)
                bundle = ({k: v for k, v in receipt.items() if k not in {'transaction_id', 'status'}}, after_body, before_body)
                if digest(tree.read(plan['target'])) != plan['after_sha256']:
                    fail('关联的已发布正文已经变化。', 'REVISION_CONFLICT')
            elif plan['operation'] == 'restore':
                receipt, source, after_body = self.receipt(tree, plan['parent_transaction'])
                if chapter.get('approval_transaction') != receipt['transaction_id']:
                    fail('当前章节已经由其他事务更新。', 'REVISION_CONFLICT')
                bundle = admin._plan(tree, 'restore', plan['source'], plan['target'], source, receipt['transaction_id'])
            else:
                source = tree.read(plan['source'])
                if digest(source) != chapter['draft']['revision'] or digest(tree.read(envelope['report_path'])) != chapter['review']['revision']:
                    fail('批准后的草稿或报告已变化。', 'REVISION_CONFLICT')
                bundle = admin._plan(tree, 'chapter_revision', plan['source'], plan['target'], source.decode('utf-8-sig').encode('utf-8'))
            if bundle[0] != plan or plan['before_sha256'] != chapter['canonical']['revision']:
                fail('正文版本已变化，请重新预览。', 'REVISION_CONFLICT')
            p, q = self._controls(tree)
            inventory = self._inventory(tree)
            known = {**{t: e['slot'] for t, e in q['entries'].items()}, **{t: e['slot'] for t, e in p['entries'].items()}}
            if any(t not in known for t in inventory):
                fail('存在尚未核查的孤立恢复记录，未发布新修订。')
            previous = q['entries'].get(associated) if associated else None
            if any(e['target'] == plan['target'] and t != associated for t, e in q['entries'].items()):
                fail('本章有未闭合的正式修订，请先处理记录。')
            if not previous and (len(q['entries']) >= SLOTS or len(p['entries']) >= SLOTS or q['used'] + RESERVE * (len(q['entries']) + 1) + 2 * CONTROL_LIMIT > HARD):
                fail('恢复容量达到上限，未发布新正式修订；草稿和正式作品保留。', 'RECOVERY_CAPACITY')
            if shutil.disk_usage(self.core.project).free < 448 * MIB:
                fail('磁盘空间不足以保留恢复证据，未发布正式修订。', 'RECOVERY_CAPACITY')
            occupied = {e['slot'] for e in q['entries'].values()} | set(inventory.values()) | {e['slot'] for e in p['entries'].values()}
            slot = previous['slot'] if previous else next(s for s in range(SLOTS) if s not in occupied)
            txn = identifier(associated) if associated else uuid.uuid4().hex
            if associated and self._directory_exists(tree, BASE + '/Archive/' + txn):
                fail('该事务已经关联，未重复登记。', 'RECOVERY_CONFLICT')
            from .lengths import evaluate
            from .text_metrics import measure
            settings = decode(tree.read('.novel-studio/Creative/settings.json'))['length_plan']
            reading = evaluate(measure(bundle[1].decode('utf-8-sig'), settings['metric'])['count'], self.core.lengths.resolved_goal(cid, settings))
            patch = {'canonical': {'document_id': digest(plan['target'].encode())[:32], 'revision': plan['after_sha256']},
                     'approval_transaction': txn, 'status': 'ARCHIVED', 'local_length': reading}
            intent = self._record(transaction_id=txn, chapter_id=cid, slot=slot, plan_sha=digest(admin.encode(plan)),
                before_state_sha=digest(raw), before_chapter=copy.deepcopy(chapter), patch=patch)
            if len(admin.encode(intent)) > CONTROL_LIMIT:
                fail('本章恢复意图超过安全预算，未发布。', 'RECOVERY_CAPACITY')
            entry = {'slot': slot, 'intent_sha': digest(admin.encode(intent)), 'archive_sha': None}
            if previous and (previous['intent_sha'] != entry['intent_sha'] or previous['context_sha'] != digest(admin.encode(chapter))):
                fail('关联意图与中断记录不一致，保留原记录。', 'RECOVERY_CONFLICT')
            q['entries'][txn] = {**entry, 'chapter_id': cid, 'target': plan['target'], 'before': chapter['canonical'],
                                 'context_sha': digest(admin.encode(chapter)), 'history_new': previous['history_new'] if previous else {}}
            tree.mkdir(BASE)
            self._ledger(tree, 'Quota', q)
            checkpoint('reserved')
            folder = self._slot(slot)
            tree.mkdir(folder)
            self._put(tree, folder, txn, 'PLAN', plan)
            self._history(tree, raw, txn, q)
            self._put(tree, folder, txn, 'INTENT', intent)
            if associated:
                self._put(tree, folder, txn, 'ASSOCIATION', self._record(transaction_id=txn, chapter_id=cid,
                    intent_sha=entry['intent_sha'], purpose='metadata_association_only'))
            self._load(tree, txn, slot, entry)
            matches = [c['chapter_id'] for c in state['chapters'].values()
                       if (c.get('canonical') or {}).get('document_id') == chapter['canonical']['document_id']]
            if matches != [cid]:
                fail('正式目标未能唯一关联本章，未发布修订。')
            checkpoint('intent')
            p['entries'][txn] = entry
            self._ledger(tree, 'Pending', p)
            checkpoint('pending')
            self._put(tree, folder, txn, 'REGISTERED', self._record(transaction_id=txn, chapter_id=cid,
                intent_sha=entry['intent_sha'], entry_sha=digest(admin.encode(entry))))
            checkpoint('registered')
            if not associated:
                receipt = admin._commit(tree, bundle, plan['approval_digest'], transaction_id=txn)
            checkpoint('body')
            self._authorize(tree, txn, intent, folder, entry['intent_sha'], raw, 'normal', q)
            state['chapters'][cid].update(patch)
            checkpoint('before_index')
            if digest(tree.read(plan['target'])) != plan['after_sha256']:
                fail('正式正文在索引发布前发生变化。', 'RECOVERY_CONFLICT')
            self.store._publish_locked(tree, raw, state, 'accept_revision_index')
            checkpoint('index')
            self._finish(tree, txn, p, q)
            return receipt

    def receipt(self, tree, txn):
        """Direct, bounded, exact transaction proof; never infer from names/time/SHA."""
        identifier(txn)
        folder = 'Backups/Transactions/' + txn
        names = []
        for index, item in enumerate(tree.children(folder)):
            if index >= 4:
                fail('正文事务文件数量超限。')
            names.append(item)
        if len(names) != 4 or {n for n, d in names if not d} != {'before.bin', 'after.bin', 'PREPARED.json', 'COMMITTED.json'}:
            fail('正文事务凭证不完整。')
        plan = decode(tree.read(folder + '/PREPARED.json'))
        receipt = decode(tree.read(folder + '/COMMITTED.json'))
        expected = {'format', 'project', 'operation', 'source', 'target', 'before_sha256', 'after_sha256', 'diff', 'parent_transaction', 'approval_digest'}
        if (set(plan) != expected or plan['format'] != admin.FORMAT or plan['project'] != str(self.core.project)
                or receipt != {**plan, 'transaction_id': txn, 'status': 'COMMITTED'}
                or digest(admin.encode({k: v for k, v in plan.items() if k != 'approval_digest'})) != sha(plan['approval_digest'])):
            fail('正文事务身份、批准摘要或完成凭证不一致。')
        self._target(plan['target'])
        before, after = tree.read(folder + '/before.bin'), tree.read(folder + '/after.bin')
        if digest(before) != sha(plan['before_sha256']) or digest(after) != sha(plan['after_sha256']):
            fail('正文事务备份摘要不匹配。')
        return receipt, before, after

    def restore_preview(self, plan):
        with self.core.mutex, SafeTree(self.core.project) as tree:
            receipt, before, after = self.receipt(tree, plan['parent_transaction'])
            state = self.store.read()
            matches = [c for c in state['chapters'].values()
                       if (c.get('canonical') or {}).get('document_id') == digest(plan['target'].encode())[:32]]
            if len(matches) != 1:
                fail('正式章节尚未建立唯一目录关联，未发布恢复正文。', 'RECOVERY_CONFLICT')
            chapter = matches[0]
            if chapter.get('approval_transaction') != receipt['transaction_id'] or chapter['canonical']['revision'] != receipt['after_sha256']:
                fail('恢复父事务不是本章当前正式版本。', 'RECOVERY_CONFLICT')
            return {'plan': plan, 'chapter_sha': digest(admin.encode(chapter))}, chapter['chapter_id']

    def _resolve(self, tree, txn, p, q):
        identifier(txn)
        entry = p['entries'].get(txn) or q['entries'].get(txn)
        if entry is None:
            # Completed lookup is direct, never an archive inventory.
            sealed = decode(tree.read(BASE + '/Archive/' + txn + '/SEALED.json'))
            entry = {'slot': sealed['slot'], 'intent_sha': sealed['intent_sha'], 'archive_sha': digest(admin.encode(sealed))}
        return entry

    def preview(self, txn, close=False):
        with self.core.mutex, SafeTree(self.core.project) as tree:
            p, q = self._controls(tree)
            entry = self._resolve(tree, txn, p, q)
            state_raw = tree.read(STATE)
            row = self._inspect(tree, txn, entry['slot'], entry, p, q, self.store.decode(state_raw))
            if not (row['can_close'] if close else row['can_recover'] or row['state'] in {'INDEX_APPLIED_AUDIT_PENDING', 'INDEX_DONE'}):
                fail('证据不足或存在冲突，未提供快捷恢复。', 'RECOVERY_CONFLICT')
            plan = {'transaction_id': txn, 'state_sha': digest(state_raw), 'intent_sha': entry['intent_sha'],
                    'evidence_sha': self._fingerprint(tree, txn, entry), 'close': bool(close)}
            handle = secrets.token_urlsafe(32)
            self.core._approvals[handle] = (time.monotonic() + 300, plan, ('canonical_recovery', txn))
            return {'handle': handle, 'operation': 'canonical_recovery', 'target': '补齐章节目录记录（不修改正文）',
                    'diff': ('关闭未发布意图并保留完整记录。' if close else '将获批事务关联回对应章节，保留正文原字节。') + '\n第' + str(row['volume']) + '卷 第' + str(row['number']) + '章 ' + row['title']}

    def commit(self, approved):
        txn = approved['transaction_id']
        with self._write() as tree:
            p, q = self._controls(tree)
            entry = self._resolve(tree, txn, p, q)
            raw = tree.read(STATE)
            if (digest(raw) != approved['state_sha'] or entry['intent_sha'] != approved['intent_sha']
                    or self._fingerprint(tree, txn, entry) != approved['evidence_sha']):
                fail('恢复预览后的记录已变化，请重新查看。', 'REVISION_CONFLICT')
            row = self._inspect(tree, txn, entry['slot'], entry, p, q, self.store.decode(raw))
            if approved['close']:
                if not row['can_close']:
                    fail('不能证明正文未发布，未关闭意图。')
                folder = self._slot(entry['slot'])
                tree.mkdir(folder)
                reservation = {**q['entries'][txn], 'archive_sha': None}
                self._put(tree, folder, txn, 'CANCELLED', self._record(transaction_id=txn, intent_sha=entry['intent_sha'],
                    reservation=reservation, before_chapter=self.store.decode(raw)['chapters'][reservation['chapter_id']],
                    observed_state_sha=digest(raw)))
                self._archive(tree, txn, p, q)
                return {'transaction_id': txn, 'state': 'CANCELLED'}
            if row['state'] == 'REGISTRY_MISSING':
                if txn not in q['entries'] or txn in p['entries']:
                    fail('不能证明缺失登记的完整来源。')
                p['entries'][txn] = {k: entry[k] for k in ('slot', 'intent_sha', 'archive_sha')}
                self._ledger(tree, 'Pending', p)
                row = self._classify(tree, txn, entry['slot'], entry, p, self.store.decode(raw))
            if row['state'] == 'INDEX_PENDING':
                if txn not in p['entries'] or txn not in q['entries']:
                    fail('登记缺失，保留证据，需核查登记完整性。')
                intent, plan, folder, intent_sha = self._load(tree, txn, entry['slot'], entry)
                self._authorize(tree, txn, intent, folder, intent_sha, raw, 'recovery', q)
                checkpoint('recovery_authorized')
                if digest(tree.read(plan['target'])) != plan['after_sha256']:
                    fail('正式正文在索引补记前发生变化。', 'RECOVERY_CONFLICT')
                state = self.store.decode(raw)
                state['chapters'][intent['chapter_id']].update(intent['patch'])
                self.store._publish_locked(tree, raw, state, 'accept_revision_recovered')
                checkpoint('recovery_index')
            elif row['state'] not in {'INDEX_APPLIED_AUDIT_PENDING', 'INDEX_DONE'}:
                fail('当前状态不能安全补记索引。', 'RECOVERY_CONFLICT')
            self._finish(tree, txn, p, q)
            return {'transaction_id': txn, 'state': 'INDEX_DONE', 'body_changed': False}

    def _fingerprint(self, tree, txn, entry):
        folder = BASE + '/Archive/' + txn if entry['archive_sha'] else self._slot(entry['slot'])
        names = [folder + '/' + n for n in self._files(tree, folder)]
        names += ['Backups/Transactions/' + txn + '/' + n for n in ('PREPARED.json', 'COMMITTED.json', 'before.bin', 'after.bin')]
        # File set, including partial intent bytes, is bound even for cancellation.
        values = {}
        for name in names:
            raw = tree.maybe_read(name)
            values[name] = digest(raw) if raw is not None else None
        return digest(admin.encode(values))

    def _inspect(self, tree, txn, slot, entry, pending, quota, state):
        """Incomplete pre-publication slots have a safe, author-confirmed exit."""
        try:
            row = self._classify(tree, txn, slot, entry, pending, state)
            if row['can_close']:
                folder = BASE + '/Archive/' + txn if entry and entry['archive_sha'] else self._slot(slot)
                if self._cancelled(tree, folder, txn, quota['entries'][txn]):
                    row.update(state='CANCELLED_CLOSING', can_close=False)
            return row
        except (ValueError, OSError, KeyError, TypeError):
            reservation = quota['entries'].get(txn)
            if not reservation:
                raise
            folder = BASE + '/Archive/' + txn if entry and entry['archive_sha'] else self._slot(slot)
            if entry and entry['archive_sha']:
                # An invalid archive pointer is not an unpublished fragment.
                self._seal(tree, txn, entry['archive_sha'])
            files = self._files(tree, folder)
            if any(FILE.fullmatch(n)[1] != txn for n in files):
                raise
            current = state['chapters'].get(reservation['chapter_id'])
            target = tree.maybe_read(reservation['target'])
            if (current is None or digest(admin.encode(current)) != reservation['context_sha']
                    or target is None or digest(target) != reservation['before']['revision']
                    or digest(reservation['target'].encode())[:32] != reservation['before']['document_id']
                    or self._directory_exists(tree, 'Backups/Transactions/' + txn)
                    or tree.maybe_read(self._path(folder, txn, 'REGISTERED')) is not None):
                raise
            cancelled = self._cancelled(tree, folder, txn, reservation)
            return {'transaction_id': txn, 'chapter_id': reservation['chapter_id'], 'slot': slot,
                    'volume': current['volume'], 'number': current['number'], 'title': current['title'],
                    'state': 'CANCELLED_CLOSING' if cancelled else 'INCOMPLETE_UNPUBLISHED',
                    'body_saved': False, 'approval_valid': False, 'index_updated': False,
                    'can_recover': False, 'can_close': not cancelled}

    def _cancelled(self, tree, folder, txn, reservation):
        raw = tree.maybe_read(self._path(folder, txn, 'CANCELLED'))
        if raw is None:
            return False
        value = self._header(decode(raw), {'transaction_id', 'intent_sha', 'reservation', 'before_chapter', 'observed_state_sha'})
        if (value['transaction_id'] != txn or value['intent_sha'] != reservation['intent_sha']
                or value['reservation'] != {**reservation, 'archive_sha': None}
                or digest(admin.encode(value['before_chapter'])) != reservation['context_sha']):
            fail('关闭未发布意图的作者确认记录无效。')
        sha(value['observed_state_sha'])
        return True

    def _finish(self, tree, txn, pending, quota):
        entry = self._resolve(tree, txn, pending, quota)
        state = self.store.decode(tree.read(STATE))
        row = self._classify(tree, txn, entry['slot'], entry, pending, state)
        if row['state'] not in {'INDEX_APPLIED_AUDIT_PENDING', 'INDEX_DONE'}:
            fail('索引或正文变化，停止补记审计。', 'RECOVERY_CONFLICT')
        intent, plan, folder, intent_sha = self._load(tree, txn, entry['slot'], entry)
        checkpoint('before_done')
        self._done(tree, folder, txn, self._record(transaction_id=txn, chapter_id=intent['chapter_id'],
            intent_sha=intent_sha, chapter_sha=digest(admin.encode({**intent['before_chapter'], **intent['patch']}))), write=True)
        checkpoint('done')
        self._archive(tree, txn, pending, quota)

    def _done(self, tree, folder, txn, expected, write=False):
        """Retain a torn audit fragment and append a complete immutable record.

        This is only used after full publication, index and authorization checks.
        It never repairs a sealed archive or replaces contradictory valid JSON.
        """
        valid = False
        for name in self._files(tree, folder):
            kind = FILE.fullmatch(name)[2]
            if kind != 'DONE' and not kind.startswith('DONE-'):
                continue
            raw = tree.read(folder + '/' + name)
            try:
                value = decode(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if value != expected:
                fail('索引完成凭证与已批准版本矛盾。')
            valid = True
        if valid:
            return True
        if write:
            kind = 'DONE' if tree.maybe_read(self._path(folder, txn, 'DONE')) is None else 'DONE-' + uuid.uuid4().hex
            self._put(tree, folder, txn, kind, expected)
            return True
        return False

    def _seal(self, tree, txn, expected):
        folder = BASE + '/Archive/' + identifier(txn)
        raw = tree.read(folder + '/SEALED.json')
        sealed = self._header(decode(raw), {'transaction_id', 'slot', 'intent_sha', 'files'})
        if digest(raw) != sha(expected) or sealed['transaction_id'] != txn or type(sealed['slot']) is not int or not 0 <= sealed['slot'] < SLOTS:
            fail('恢复存档身份或摘要不一致。')
        sha(sealed['intent_sha'])
        if not isinstance(sealed['files'], dict) or not 1 <= len(sealed['files']) <= MAX_FILES:
            fail('恢复存档数量无效。')
        actual_files = self._files(tree, folder)
        if set(actual_files) != set(sealed['files']):
            fail('存档有未登记或缺失的文件，未清理源文件。')
        kinds = {FILE.fullmatch(name)[2] for name in actual_files}
        has_done = any(k == 'DONE' or k.startswith('DONE-') for k in kinds)
        if int(has_done) + int('CANCELLED' in kinds) != 1:
            fail('存档缺少唯一的关闭凭证。')
        for name, expected_sha in sealed['files'].items():
            match = FILE.fullmatch(name)
            if not match or match[1] != txn or digest(tree.read(folder + '/' + name)) != sha(expected_sha):
                fail('恢复存档文件不匹配，未清理源文件。')
        return sealed

    def _archive(self, tree, txn, pending, quota):
        entry = pending['entries'].get(txn) or quota['entries'].get(txn)
        if entry is None:
            return
        slot = entry['slot']
        source, target = self._slot(slot), BASE + '/Archive/' + txn
        files = self._files(tree, source)
        if any(FILE.fullmatch(n)[1] != txn for n in files):
            fail('槽位有其他操作文件，未清理。')
        if entry['archive_sha'] is None:
            if not any(FILE.fullmatch(name)[2] in {'DONE', 'CANCELLED'} or FILE.fullmatch(name)[2].startswith('DONE-') for name in files):
                fail('恢复尚未闭合，不能存档清理。')
            tree.mkdir(target)
            mapping = {}
            for name in files:
                raw = tree.read(source + '/' + name)
                mapping[name] = digest(raw)
                previous = tree.maybe_read(target + '/' + name)
                if previous is None:
                    tree.create(target + '/' + name, raw)
                elif previous != raw:
                    fail('已有恢复存档与源文件不同。')
            checkpoint('archive_copied')
            sealed = self._record(transaction_id=txn, slot=slot, intent_sha=entry['intent_sha'], files=mapping)
            raw = admin.encode(sealed)
            old = tree.maybe_read(target + '/SEALED.json')
            if old is None:
                tree.create(target + '/SEALED.json', raw)
            elif old != raw:
                fail('不能覆盖已有存档封口凭证。')
            archive_sha = digest(raw)
        else:
            archive_sha = entry['archive_sha']
        sealed = self._seal(tree, txn, archive_sha)
        if txn in pending['entries']:
            pending['entries'][txn]['archive_sha'] = archive_sha
            self._ledger(tree, 'Pending', pending)
        checkpoint('pending_archive')
        if txn in quota['entries']:
            quota['entries'][txn]['archive_sha'] = archive_sha
            self._ledger(tree, 'Quota', quota)
        checkpoint('quota_archive')
        # Both indexes point to the verified permanent copy before deletion.
        for name in files:
            self._seal(tree, txn, archive_sha)
            if name not in sealed['files']:
                fail('槽位内容不属于已封存副本。')
            tree._discard_internal(source + '/' + name, sealed['files'][name])
            checkpoint('cleanup_one')
        if txn in pending['entries']:
            del pending['entries'][txn]
            self._ledger(tree, 'Pending', pending)
        checkpoint('pending_removed')
        if txn in quota['entries']:
            cost = sum(len(tree.read(target + '/' + n)) for n in sealed['files']) + len(tree.read(target + '/SEALED.json'))
            cost += sum(len(tree.maybe_read(WORKFLOW + '/History/' + key + '.json') or b'')
                        + len(tree.maybe_read(BASE + '/Charges/' + key + '.json') or b'')
                        for key in quota['entries'][txn]['history_new'])
            if cost > RESERVE or quota['used'] + cost > HARD:
                fail('恢复实际用量超过预留，保留容量登记。', 'RECOVERY_CAPACITY')
            quota['used'] += cost
            del quota['entries'][txn]
            self._ledger(tree, 'Quota', quota)
        checkpoint('quota_released')

    def retry_audits(self):
        """Only previously authorized index writes; never applies an index patch."""
        results = []
        for row in self.diagnose()['operations']:
            if row['state'] not in {'INDEX_APPLIED_AUDIT_PENDING', 'INDEX_DONE', 'CANCELLED_CLOSING'}:
                continue
            try:
                with self._write() as tree:
                    p, q = self._controls(tree)
                    if row['state'] == 'CANCELLED_CLOSING':
                        entry = self._resolve(tree, row['transaction_id'], p, q)
                        verified = self._inspect(tree, row['transaction_id'], entry['slot'], entry, p, q, self.store.decode(tree.read(STATE)))
                        if verified['state'] != 'CANCELLED_CLOSING':
                            fail('关闭意图的证据已变化。')
                        self._archive(tree, row['transaction_id'], p, q)
                    else:
                        self._finish(tree, row['transaction_id'], p, q)
                    results.append(row['transaction_id'])
            except (ValueError, OSError):
                continue  # Keep all evidence, retry on a later explicit/UI tick.
        return results
