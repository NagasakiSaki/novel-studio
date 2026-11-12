"""Author-only phase-two adapters over the existing recovery/Workflow protocol.

No recovery method writes Canonical. Only submit() (inherited) publishes an
explicitly approved new Restore. All writes share the existing project lock.
"""
import copy
from pathlib import Path
import secrets
import time
import uuid

from .canonical_recovery import (CanonicalRecovery, BASE, STATE, CONTROL_LIMIT,
                                 HARD, RESERVE, MIB, SafeTree, admin, digest,
                                 decode, fail, checkpoint, sha)
from .workflow_store import BASE as WORKFLOW, identifier

EXT = WORKFLOW + '/Recovery-Extensions'
CREATIVE = '.novel-studio/Creative/settings.json'
DESKTOP = '.novel-studio/Desktop.json'


class RecoveryExtensions(CanonicalRecovery):
    def _identity(self, tree):
        if decode(tree.read('.novel-studio/project.json')) != self.core.metadata:
            fail('作品身份已经变化，请重新打开作品。', 'RECOVERY_CONFLICT')

    def _immutable(self, tree, path, payload, *, backup=False):
        """Charge before creation. Crashes may over-reserve, never under-charge.

        Existing records are immutable; no history cleanup or quota expansion.
        Individual metadata <=256KiB, each original-byte backup <=32MiB.
        """
        workflow_backup = backup and path == WORKFLOW + '/History/' + digest(payload) + '.json'
        if not path.startswith(EXT + '/') and not workflow_backup:
            fail('恢复记录路径无效。')
        if len(payload) > (32 * MIB if backup else CONTROL_LIMIT):
            fail('恢复记录超过单次预算。', 'RECOVERY_CAPACITY')
        old = tree.maybe_read(path)
        if old is not None:
            if old != payload:
                fail('已有恢复凭证不同，未覆盖。', 'RECOVERY_CONFLICT')
            return
        p, q = self._controls(tree)
        if q['used'] + len(payload) + RESERVE * len(q['entries']) + CONTROL_LIMIT > HARD:
            fail('恢复容量达到上限，保留现有作品和全部凭证。', 'RECOVERY_CAPACITY')
        q['used'] += len(payload)
        tree.mkdir(BASE)
        self._ledger(tree, 'Quota', q)
        tree.mkdir(Path(path).parent.as_posix())
        tree.create(path, payload)
        if tree.read(path) != payload:
            fail('恢复凭证回读失败。')

    def _offer(self, operation, value, description):
        handle = secrets.token_urlsafe(32)
        self.core._approvals[handle] = (time.monotonic() + 300, value, (operation, None))
        return {'handle': handle, 'operation': operation, 'target': '仅恢复本作品的目录和显示记录', 'diff': description}

    def legacy_preview(self, project, txn, cid):
        """Explicit current-project association, never cross-project migration.

        A legacy chapter with no recorded approval transaction can be bound by
        the author. A known successor without a proven parent edge is refused.
        """
        identifier(cid); identifier(txn)
        if Path(project).resolve() != self.core.project:
            fail('所选旧工程不是当前作品；不会自动关联其他作品。', 'RECOVERY_CONFLICT')
        with self.core.mutex, SafeTree(self.core.project) as tree:
            self._identity(tree)
            state = self.store.decode(tree.read(STATE))
            chapter = state['chapters'].get(cid)
            receipt, before, after = self.receipt(tree, txn)
            if (chapter is not None and chapter.get('approval_transaction') == txn
                    and chapter.get('canonical', {}).get('revision') == digest(after)):
                p, q = self._controls(tree)
                if txn in p['entries'] or txn in q['entries'] or tree.maybe_read(BASE + '/Archive/' + txn + '/SEALED.json'):
                    return self.preview(txn)
                if chapter['canonical']['document_id'] != digest(receipt['target'].encode())[:32] or digest(tree.read(receipt['target'])) != digest(after):
                    fail('旧目录与正文实际来源不符。')
                return self._offer('legacy_verified', {'transaction_id':txn, 'chapter_id':cid,
                    'chapter_sha':digest(admin.encode(chapter)), 'receipt_sha':digest(admin.encode(receipt))},
                    '这笔旧事务已经精确关联到所选章节，正文和目录一致。本次只保存核验记录，不重复改目录或正文。')
            revision = (chapter is not None and receipt['operation'] == 'chapter_revision'
                and receipt['parent_transaction'] is None and chapter.get('approval_transaction') is None
                and (chapter.get('draft') or {}).get('document_id') == digest(receipt['source'].encode())[:32])
            restore = (chapter is not None and receipt['operation'] == 'restore'
                and chapter.get('approval_transaction') == receipt['parent_transaction'])
            if restore:
                parent = self.receipt(tree, receipt['parent_transaction'])[0]
                restore = (receipt['source'] == 'Backups/Transactions/' + parent['transaction_id'] + '/before.bin'
                    and receipt['target'] == parent['target'] and receipt['before_sha256'] == parent['after_sha256']
                    and receipt['after_sha256'] == parent['before_sha256'])
            if (chapter is None or not (revision or restore)
                    or chapter.get('canonical') != {'document_id': digest(receipt['target'].encode())[:32], 'revision': digest(before)}
                    or digest(tree.read(receipt['target'])) != digest(after)):
                fail('旧记录不能证明本章的唯一来源，或已有后继事务；未提供快捷关联。', 'RECOVERY_CONFLICT')
            plan = {k: v for k, v in receipt.items() if k not in {'transaction_id', 'status'}}
            value = {'plan': plan, 'chapter_sha': digest(admin.encode(chapter)), 'associated_transaction': txn}
            # Validate every source/type/patch using the same loader at commit.
            if revision:
                self._reference(chapter.get('review'))
            return self._offer('legacy_association', {'envelope': value, 'chapter_id': cid},
                f"作品：{self.core.title}\n第{chapter['volume']}卷 第{chapter['number']}章 {chapter['title']}\n"
                f"来源：本作品完整事务 {txn}\n本次批准的是旧记录与章节目录的关联，不追认缺失的历史发布批准。\n"
                '保留原正文、原事务和历史版本；不生成、不发布、不回滚正文。')

    def associate(self, approved):
        return self.submit(approved['envelope'], approved['chapter_id'])

    def verify_association(self, approved):
        with self._write() as tree:
            self._identity(tree)
            txn, cid = identifier(approved['transaction_id']), identifier(approved['chapter_id'])
            receipt, before, after = self.receipt(tree, txn)
            chapter = self.store.decode(tree.read(STATE))['chapters'][cid]
            if (digest(admin.encode(chapter)) != approved['chapter_sha'] or digest(admin.encode(receipt)) != approved['receipt_sha']
                    or chapter.get('approval_transaction') != txn
                    or chapter.get('canonical') != {'document_id':digest(receipt['target'].encode())[:32], 'revision':digest(after)}
                    or digest(tree.read(receipt['target'])) != digest(after)):
                fail('旧关联在预览后发生变化。', 'RECOVERY_CONFLICT')
            record = self._record(**approved, purpose='verify_existing_index_only')
            self._immutable(tree, EXT + '/Legacy-Verified/' + txn + '/' + approved['chapter_sha'] + '.json', admin.encode(record))
            return {'transaction_id':txn, 'state':'INDEX_ALREADY_LINKED', 'body_changed':False}

    def complete_derived(self, cid):
        """Normal author-approved publication's derived tail, with durable retry."""
        with self.core.mutex, SafeTree(self.core.project) as tree:
            plan, payloads = self._derived_plan(tree, cid)
            approved = {'plan': plan, 'observed': {n: digest(tree.read(n)) for n in payloads}}
        return self.recover_derived(approved)

    def prepare_batch(self, tree, bundle, state):
        """Called inside WorkflowStore's existing lock before any body write."""
        self._identity(tree)
        bid = identifier(bundle['batch_id'])
        items = bundle['items']
        if not 1 <= len(items) <= 100 or len({i['chapter_id'] for i in items}) != len(items):
            fail('收录批次超过数量限制或存在重复章节。')
        record = self._record(batch_id=bid, bundle_sha=digest(admin.encode(bundle)), mode='new',
            transactions={i['chapter_id']: uuid.uuid4().hex for i in items},
            chapters={i['chapter_id']: copy.deepcopy(state['chapters'][i['chapter_id']]) for i in items})
        self._immutable(tree, EXT + '/Batches/' + bid + '.json', admin.encode(record))
        checkpoint('batch_intent')
        return record

    def _batch(self, tree, state, bid):
        identifier(bid)
        ref = state['batches'].get(bid)
        if not ref or ref.get('path') != WORKFLOW + '/Batches/' + bid + '.json':
            fail('收录批次身份不匹配。')
        raw = tree.read(ref['path'])
        if digest(raw) != ref['sha256']:
            fail('批次清单摘要不匹配。')
        bundle = decode(raw)
        if set(bundle) != {'batch_id', 'outline_version', 'items'} or bundle['batch_id'] != bid:
            fail('批次清单格式无效。')
        items = bundle['items']
        if not isinstance(items, list) or not 1 <= len(items) <= 100:
            fail('批次文件数量超过限制。')
        record_raw = tree.maybe_read(EXT + '/Batches/' + bid + '.json')
        if record_raw is None:
            fail('旧批次缺少精确事务登记，需逐项核查；未扫描历史或推测归属。', 'BATCH_UNCERTAIN')
        record = self._header(decode(record_raw), {'batch_id', 'bundle_sha', 'transactions', 'chapters', 'mode'})
        ids = [identifier(i['chapter_id']) for i in items]
        if (len(raw) > 32 * MIB or len(record_raw) > CONTROL_LIMIT or record['batch_id'] != bid
                or record['mode'] not in {'new', 'legacy'}
                or record['bundle_sha'] != digest(raw) or len(set(ids)) != len(ids)
                or set(record['transactions']) != set(ids) or set(record['chapters']) != set(ids)
                or len(set(record['transactions'].values())) != len(ids)):
            fail('批次来源或事务登记不一致。')
        for txn in record['transactions'].values():
            identifier(txn)
        for item in items:
            cid = item['chapter_id']
            before = record['chapters'][cid]
            volume, number = item['volume'], item['number']
            existing = before.get('canonical')
            if existing is not None and (record['mode'] != 'legacy'
                    or existing != {'document_id': digest(item['plan']['target'].encode())[:32], 'revision': item['plan']['after_sha256']}
                    or before.get('approval_transaction') != record['transactions'][cid]):
                fail('旧批次关联不能替代后继事务。')
            if (type(volume) is not int or type(number) is not int or not 1 <= volume <= 100 or not 1 <= number <= 1000
                    or before.get('chapter_id') != cid
                    or before.get('draft') != item['draft'] or before.get('review') != item['report']
                    or item['plan']['target'] != f'Canonical/Final-Manuscript/Volume-{volume:03d}/Chapter-{number:03d}.md'
                    or item['plan']['source'] != item['source']
                    or admin.relative_parts(item['source'])[:2] != ('Writer-Workspace', 'Drafts')
                    or admin.relative_parts(item['report_source'])[0] != 'Editor-Workspace'):
                fail('批次章节身份、来源或正式编号不一致。')
        return bundle, record

    def legacy_transactions_preview(self, project, transactions, cid):
        if not isinstance(transactions, list) or not 1 <= len(transactions) <= 100 or len(set(transactions)) != len(transactions):
            fail('一次请选择不超过 100 份不同的完整事务凭证。')
        if Path(project).resolve() != self.core.project:
            fail('所选旧工程不属于当前作品。', 'RECOVERY_CONFLICT')
        with self.core.mutex, SafeTree(self.core.project) as tree:
            self._identity(tree)
            first = decode(tree.read('Backups/Transactions/' + identifier(transactions[0]) + '/PREPARED.json'))
            if first.get('operation') != 'chapter_accept':
                if len(transactions) != 1:
                    fail('修订旧记录请每次选择一笔完整事务。')
                return self.legacy_preview(project, transactions[0], cid)
            bid = identifier(first['parent_transaction'])
            state_raw = tree.read(STATE)
            state = self.store.decode(state_raw)
            ref = state['batches'].get(bid)
            if not ref or ref.get('path') != WORKFLOW + '/Batches/' + bid + '.json':
                fail('本作品没有该旧批次的原始批准清单。')
            bundle_raw = tree.read(ref['path'])
            bundle = decode(bundle_raw)
            if (digest(bundle_raw) != ref['sha256'] or set(bundle) != {'batch_id', 'outline_version', 'items'}
                    or bundle['batch_id'] != bid or not 1 <= len(bundle['items']) <= 100):
                fail('旧批次清单损坏或超过安全限制。')
            mapping = {}
            for txn in transactions:
                identifier(txn)
                prepared = decode(tree.read('Backups/Transactions/' + txn + '/PREPARED.json'))
                matches = [i for i in bundle['items'] if i['plan'] == prepared]
                if len(matches) != 1 or prepared['parent_transaction'] != bid:
                    fail('所选事务与本作品旧批次不一致。')
                item = matches[0]
                self._accept_receipt(tree, txn, item['plan'])
                if item['chapter_id'] in mapping or digest(tree.read(prepared['target'])) != prepared['after_sha256']:
                    fail('旧批次有重复归属或外部修改。')
                mapping[item['chapter_id']] = txn
            if tree.maybe_read(EXT + '/Batches/' + bid + '.json') is not None:
                record = self._batch(tree, state, bid)[1]
                if any(record['transactions'][key] != value for key, value in mapping.items()):
                    fail('已关联的批次与所选旧事务不同。')
                return self.batches_preview([bid])
            for item in bundle['items']:
                chapter = state['chapters'][item['chapter_id']]
                self._target(item['plan']['target'])
                actual = tree.maybe_read(item['plan']['target'])
                txn = mapping.get(item['chapter_id'])
                if txn is None:
                    if actual is not None or chapter.get('canonical') is not None or chapter.get('approval_transaction') is not None:
                        fail('旧批次还有已发布但未选择完整凭证的章节；请保留全部记录并一并核查。')
                    mapping[item['chapter_id']] = uuid.uuid4().hex
                elif chapter.get('canonical') is not None and (
                        chapter['canonical'] != {'document_id': digest(item['plan']['target'].encode())[:32], 'revision': item['plan']['after_sha256']}
                        or chapter.get('approval_transaction') != txn):
                    fail('旧批次已有其他正式版本，不能覆盖目录。')
                if chapter.get('draft') != item['draft'] or chapter.get('review') != item['report']:
                    fail('旧批次章节来源已变化，未推测关联。')
            record = self._record(batch_id=bid, bundle_sha=digest(bundle_raw), mode='legacy', transactions=mapping,
                                  chapters={i['chapter_id']:copy.deepcopy(state['chapters'][i['chapter_id']]) for i in bundle['items']})
            return self._offer('legacy_batch_association', {'record':record, 'state_sha':digest(state_raw)},
                f'当前作品的旧收录批次：{len(bundle["items"])} 章，已明确选择 {len(transactions)} 笔完整事务。\n'
                '将保留原始批次、原事务和正文，建立精确关联并只补目录。未发布项不会自动续发。')

    def associate_batch(self, approved):
        record = approved['record']
        bid = identifier(record['batch_id'])
        with self._write() as tree:
            self._identity(tree)
            if digest(tree.read(STATE)) != approved['state_sha']:
                fail('旧批次预览后章节目录已变化。', 'REVISION_CONFLICT')
            state = self.store.decode(tree.read(STATE))
            ref = state['batches'][bid]
            raw = tree.read(ref['path'])
            if digest(raw) != record['bundle_sha'] or digest(raw) != ref['sha256']:
                fail('旧批次清单已经变化。')
            bundle = decode(raw)
            for item in bundle['items']:
                txn = record['transactions'][item['chapter_id']]
                actual = tree.maybe_read(item['plan']['target'])
                if actual is not None:
                    self._accept_receipt(tree, txn, item['plan'])
                    if digest(actual) != item['plan']['after_sha256']:
                        fail('旧正文在确认前变化，未覆盖。')
                elif self._directory_exists(tree, 'Backups/Transactions/' + txn):
                    fail('旧事务状态不明确，保留原凭证。')
            self._immutable(tree, EXT + '/Batches/' + bid + '.json', admin.encode(record))
            checkpoint('legacy_batch_associated')
        return self.recover_batch(bid, True)

    def extended_diagnose(self, offset=0, limit=25):
        """Read-only bounded current-index diagnosis, no archive enumeration."""
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 25:
            fail('恢复分页范围无效。')
        with self.core.mutex, SafeTree(self.core.project) as tree:
            self._identity(tree)
            state = self.store.decode(tree.maybe_read(STATE))
            settings_raw = tree.maybe_read(CREATIVE)
            settings = decode(settings_raw) if settings_raw else {}
            desktop_raw = tree.maybe_read(DESKTOP)
            desktop = decode(desktop_raw) if desktop_raw else {}
            batches = [{'batch_id': bid, 'completed': len(row.get('completed', []))}
                       for bid, row in state['batches'].items() if row['status'] != 'COMPLETED']
            chapters = sorted(state['chapters'].values(), key=lambda c:c['ordinal'])
            derived = []
            for chapter in chapters[offset:offset + limit]:
                txn = chapter.get('approval_transaction')
                if not chapter.get('canonical') or txn is None:
                    continue
                identifier(txn)
                source = settings.get('length_plan', {}).get('nodes', {}).get(chapter['chapter_id'], {}).get('source')
                pointer_raw = tree.maybe_read(EXT + '/Derived/' + txn + '/CURRENT.json')
                unfinished = False
                if pointer_raw:
                    pointer = self._header(decode(pointer_raw), {'transaction_id', 'chapter_id', 'job_id', 'plan_sha'})
                    if pointer['transaction_id'] != txn or pointer['chapter_id'] != chapter['chapter_id']:
                        fail('派生任务身份不符。')
                    folder = EXT + '/Derived/' + txn + '/' + identifier(pointer['job_id'])
                    unfinished = tree.maybe_read(folder + '/DONE.json') is None
                if source != chapter['canonical'] or txn not in desktop.get('version_order', []) or unfinished:
                    derived.append({'chapter_id': chapter['chapter_id'], 'volume': chapter['volume'], 'number': chapter['number']})
            return {'batches': batches[:limit], 'derived': derived,
                    'more': len(batches) > limit or len(chapters) > offset + limit}

    def _accept_receipt(self, tree, txn, plan):
        """Acceptance has no before.bin; all three fixed files are required."""
        folder = 'Backups/Transactions/' + identifier(txn)
        names = []
        for index, item in enumerate(tree.children(folder)):
            if index >= 3:
                fail('收录事务文件数量超限。', 'BATCH_UNCERTAIN')
            names.append(item)
        if len(names) != 3 or {n for n, d in names if not d} != {'after.bin', 'PREPARED.json', 'COMMITTED.json'}:
            fail('收录事务凭证尚未完整闭合。', 'BATCH_UNCERTAIN')
        prepared = decode(tree.read(folder + '/PREPARED.json'))
        receipt = decode(tree.read(folder + '/COMMITTED.json'))
        expected_fields = {'format', 'project', 'operation', 'source', 'target', 'before_sha256', 'after_sha256', 'diff', 'parent_transaction', 'approval_digest'}
        if (set(plan) != expected_fields or prepared != plan or receipt != {**plan, 'transaction_id': txn, 'status': 'COMMITTED'}
                or plan.get('operation') != 'chapter_accept' or plan.get('project') != str(self.core.project)
                or plan.get('format') != admin.FORMAT or plan.get('before_sha256') is not None
                or digest(admin.encode({k: v for k, v in plan.items() if k != 'approval_digest'})) != plan.get('approval_digest')
                or digest(tree.read(folder + '/after.bin')) != plan['after_sha256']):
            fail('收录事务与批准清单不一致。', 'BATCH_UNCERTAIN')
        self._target(plan['target'])
        if admin.relative_parts(plan['source'])[:2] != ('Writer-Workspace', 'Drafts'):
            fail('收录来源不属于本作品草稿。')
        return receipt

    def recover_batch(self, bid, confirmed=False):
        if not confirmed:
            fail('请先查看批次并明确确认；不会自动续发剩余正文。', 'APPROVAL_REQUIRED')
        with self._write() as tree:
            self._identity(tree)
            raw = tree.read(STATE)
            state = self.store.decode(raw)
            bundle, record = self._batch(tree, state, bid)
            recovered = []
            changed = False
            for item in bundle['items']:
                cid = item['chapter_id']
                plan = item['plan']
                self._target(plan['target'])
                if (plan['parent_transaction'] != bid or item['source'] != plan['source']
                        or item['draft']['revision'] != plan['after_sha256']):
                    fail('批次章节来源不一致。', 'BATCH_UNCERTAIN')
                txn = record['transactions'][cid]
                txn_exists = self._directory_exists(tree, 'Backups/Transactions/' + txn)
                actual = tree.maybe_read(plan['target'])
                current = state['chapters'][cid]
                before = record['chapters'][cid]
                if not txn_exists:
                    if actual is not None or current != before:
                        fail('未发布项出现外部文件或章节变化，未继续。', 'BATCH_UNCERTAIN')
                    continue
                receipt = self._accept_receipt(tree, txn, plan)
                expected_state = {'chapters': {cid: copy.deepcopy(before)}}
                self.core.workflow.chapters._index_receipt(expected_state, item, receipt)
                expected = expected_state['chapters'][cid]
                if actual is None or digest(actual) != plan['after_sha256'] or current not in (before, expected):
                    fail('正文、章节或后继事务与批准批次冲突。', 'BATCH_UNCERTAIN')
                if current != expected:
                    state['chapters'][cid] = expected
                    changed = True
                recovered.append(cid)
            expected_batch = {**state['batches'][bid], 'completed': recovered,
                              'status': 'COMPLETED' if len(recovered) == len(bundle['items']) else 'PARTIAL'}
            changed |= state['batches'][bid] != expected_batch
            if changed:
                # The immutable confirmation survives a failure before publication.
                approval = self._record(batch_id=bid, manifest_sha=record['bundle_sha'])
                self._immutable(tree, EXT + '/Batch-Confirmations/' + bid + '.json', admin.encode(approval))
                # Reuse WorkflowStore's content-addressed History. Charge new
                # recovery backups before index publication, never duplicate it.
                self._immutable(tree, WORKFLOW + '/History/' + digest(raw) + '.json', raw, backup=True)
                state['batches'][bid] = expected_batch
                checkpoint('batch_before_index')
                # Re-read bodies immediately before atomic index publication.
                for item in bundle['items']:
                    if item['chapter_id'] in recovered and digest(tree.read(item['plan']['target'])) != item['plan']['after_sha256']:
                        fail('索引发布前正文发生外部变化。', 'BATCH_UNCERTAIN')
                self.store._publish_locked(tree, raw, state, 'accept_batch_recovered')
                checkpoint('batch_index')
            result = {'recovered': recovered, 'remaining_need_new_author_preview': len(bundle['items']) - len(recovered)}
            done = self._record(batch_id=bid, manifest_sha=record['bundle_sha'], **result)
            key = digest(admin.encode(done))
            self._immutable(tree, EXT + '/Batch-Audits/' + bid + '-' + key + '.json', admin.encode(done))
            self.core.clear_read_caches()
            return result

    def batches_preview(self, ids):
        if not isinstance(ids, list) or not 1 <= len(ids) <= 16 or len(set(ids)) != len(ids):
            fail('一次请选择 1 至 16 个不同批次。', 'RECOVERY_CAPACITY')
        with self.core.mutex, SafeTree(self.core.project) as tree:
            state_raw = tree.read(STATE)
            state = self.store.decode(state_raw)
            refs, count = {}, 0
            for bid in ids:
                bundle, record = self._batch(tree, state, bid)
                refs[bid] = record['bundle_sha']
                count += len(bundle['items'])
            if count > 500:
                fail('一组最多核查 500 章，请分组处理。', 'RECOVERY_CAPACITY')
            return self._offer('recovery_batches', {'batches': refs, 'state_sha': digest(state_raw)},
                f'核查 {len(ids)} 个批次、{count} 章。逐批补记已有完整事务的目录；已完成项不重复写入。\n'
                '未发布正文仍需重新预览批准；本操作不会发布、覆盖或回滚正文。')

    def recover_batches(self, approved):
        # The manifest is a durable group approval. Each batch revalidates under
        # its own lock; no nested WorkflowStore mutation, no cross-batch atomicity.
        refs = approved['batches']
        with self._write() as tree:
            raw = tree.read(STATE)
            if digest(raw) != approved['state_sha']:
                fail('批次预览后目录已变化，请刷新。', 'REVISION_CONFLICT')
            self._identity(tree)
            state = self.store.decode(raw)
            for bid, key in refs.items():
                if self._batch(tree, state, bid)[1]['bundle_sha'] != key:
                    fail('批次来源已变化。')
            group = self._record(batches=refs)
            gid = digest(admin.encode(group))[:32]
            self._immutable(tree, EXT + '/Groups/' + gid + '.json', admin.encode(group))
        results = {}
        for bid in refs:
            results[bid] = self.recover_batch(bid, True)
            checkpoint('between_batches')
        complete = all(r['remaining_need_new_author_preview'] == 0 for r in results.values())
        if complete:
            # A different window may have acted between batches. Do not report
            # group success from an earlier member's stale in-memory result.
            with self._write() as tree:
                self._identity(tree)
                state = self.store.decode(tree.read(STATE))
                for bid, key in refs.items():
                    bundle, record = self._batch(tree, state, bid)
                    if record['bundle_sha'] != key or state['batches'][bid]['status'] != 'COMPLETED':
                        fail('批次完成状态在核查期间发生变化。', 'RECOVERY_CONFLICT')
                    for item in bundle['items']:
                        cid = item['chapter_id']
                        receipt = self._accept_receipt(tree, record['transactions'][cid], item['plan'])
                        expected = {'chapters':{cid:copy.deepcopy(record['chapters'][cid])}}
                        self.core.workflow.chapters._index_receipt(expected, item, receipt)
                        if (state['chapters'][cid] != expected['chapters'][cid]
                                or digest(tree.read(receipt['target'])) != receipt['after_sha256']):
                            fail('已有批次出现后继变更，不能宣称整组完成。', 'RECOVERY_CONFLICT')
        return {'group_id': gid, 'batches': results,
                'complete': complete}

    def derived_candidates(self, offset=0, limit=25):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 25:
            fail('请选择有效分页范围。')
        # Only current Workflow entries, never transaction/archive enumeration.
        chapters = sorted(self.store.read()['chapters'].values(), key=lambda c: c['ordinal'])
        return [{'chapter_id': c['chapter_id'], 'volume': c['volume'], 'number': c['number'], 'title': c['title']}
                for c in chapters[offset:offset + limit] if c.get('canonical') and c.get('approval_transaction')]

    def _derived_sources(self, tree, cid):
        self._identity(tree)
        state = self.store.decode(tree.read(STATE))
        chapter = state['chapters'][identifier(cid)]
        txn = identifier(chapter['approval_transaction'])
        prepared = decode(tree.read('Backups/Transactions/' + txn + '/PREPARED.json'))
        if prepared['operation'] == 'chapter_accept':
            bundle, record = self._batch(tree, state, identifier(prepared['parent_transaction']))
            item = next(i for i in bundle['items'] if i['chapter_id'] == cid)
            if record['transactions'][cid] != txn:
                fail('派生来源事务与章节不一致。')
            receipt = self._accept_receipt(tree, txn, item['plan'])
        else:
            receipt = self.receipt(tree, txn)[0]
            # A receipt and SHA alone do not prove a revision's chapter identity.
            p, q = self._controls(tree)
            entry = self._resolve(tree, txn, p, q)
            intent, plan, folder, key = self._load(tree, txn, entry['slot'], entry)
            if intent['chapter_id'] != cid or self._classify(tree, txn, entry['slot'], entry, p, state)['state'] not in {'INDEX_DONE', 'INDEX_APPLIED_AUDIT_PENDING'}:
                fail('正式索引尚未核验完成，不能重建派生数据。')
        if (chapter['canonical'] != {'document_id': digest(receipt['target'].encode())[:32], 'revision': receipt['after_sha256']}
                or digest(tree.read(receipt['target'])) != receipt['after_sha256']):
            fail('派生来源已经变化，未使用缓存恢复。', 'RECOVERY_CONFLICT')
        return chapter, txn

    def _derived_plan(self, tree, cid):
        chapter, txn = self._derived_sources(tree, cid)
        current_path = EXT + '/Derived/' + txn + '/CURRENT.json'
        pointer_raw = tree.maybe_read(current_path)
        raw = None
        if pointer_raw is not None:
            pointer = self._header(decode(pointer_raw), {'transaction_id', 'chapter_id', 'job_id', 'plan_sha'})
            if pointer['transaction_id'] != txn or pointer['chapter_id'] != cid or len(pointer_raw) > CONTROL_LIMIT:
                fail('派生任务所属章节不一致。')
            folder = EXT + '/Derived/' + txn + '/' + identifier(pointer['job_id'])
            raw = tree.read(folder + '/PLAN.json')
            if digest(raw) != pointer['plan_sha']:
                fail('派生任务凭证摘要不符。')
            done_raw = tree.maybe_read(folder + '/DONE.json')
            if done_raw is not None:
                done = self._header(decode(done_raw), {'plan_sha'})
                if done['plan_sha'] != digest(raw):
                    fail('派生完成凭证不符。')
                # A completed job cannot constrain later author settings. A
                # fresh, exact preview is required for a new derived repair.
                raw = None
        if raw is not None:
            plan = self._header(decode(raw), {'chapter_id', 'transaction_id', 'chapter_sha', 'files'})
            if (len(raw) > CONTROL_LIMIT or plan['chapter_id'] != cid or plan['transaction_id'] != txn
                    or plan['chapter_sha'] != digest(admin.encode(chapter))):
                fail('派生修复凭证与当前章节不一致。')
            if set(plan['files']) != {CREATIVE, DESKTOP}:
                fail('派生修复目标超出允许范围。')
            for target, entry in plan['files'].items():
                if set(entry) != {'before_sha', 'after_sha'}:
                    fail('派生修复文件清单无效。')
                for key in entry.values():
                    sha(key)
                    payload = tree.read(EXT + '/History/' + key + '.json')
                    if digest(payload) != key:
                        fail('派生原字节备份损坏。')
                if digest(tree.read(target)) not in entry.values():
                    fail('界面设置已由其他操作修改，保留现状。', 'RECOVERY_CONFLICT')
            # Recompute exact allowed field changes even for a local manifest.
            originals = {name: decode(tree.read(EXT + '/History/' + e['before_sha'] + '.json')) for name, e in plan['files'].items()}
        else:
            originals = {name: decode(tree.read(name)) for name in (CREATIVE, DESKTOP)}
            plan = self._record(chapter_id=cid, transaction_id=txn, chapter_sha=digest(admin.encode(chapter)), files={})
        changed = copy.deepcopy(originals)
        if changed[CREATIVE].get('format') != 'novel-studio-creative-v1':
            fail('字数与风格设置版本尚不支持，未降级或覆盖。')
        node = changed[CREATIVE]['length_plan']['nodes'].get(cid)
        if node is None or node.get('kind') != 'chapter':
            fail('字数规划中没有本章的可信身份，未猜测关联。')
        node['source'] = chapter['canonical']
        order = changed[DESKTOP].get('version_order', [])
        if not isinstance(order, list) or len(order) > 1000 or len(set(order)) != len(order):
            fail('显示顺序记录无效，未覆盖。')
        for value in order:
            identifier(value)
        if txn not in order:
            changed[DESKTOP]['version_order'] = (order + [txn])[-1000:]
        payloads = {}
        for target in (CREATIVE, DESKTOP):
            before = tree.read(EXT + '/History/' + plan['files'][target]['before_sha'] + '.json') if raw else tree.read(target)
            after = admin.encode(changed[target]) if changed[target] != originals[target] else before
            entry = {'before_sha': digest(before), 'after_sha': digest(after)}
            if raw and plan['files'][target] != entry:
                fail('派生清单包含未经许可的设置修改。')
            plan['files'][target] = entry
            payloads[target] = (before, after)
        return plan, payloads

    def derived_preview(self, cid):
        with self.core.mutex, SafeTree(self.core.project) as tree:
            plan, payloads = self._derived_plan(tree, cid)
            return self._offer('recovery_derived', {'plan': plan, 'observed': {n: digest(tree.read(n)) for n in payloads}},
                '从已核验的正式正文事务重建本章字数关联及版本显示顺序，并清除本窗口可重建的读取缓存。\n'
                '不修改正文、草稿、设定、作者风格或其他章节；设置原字节及恢复凭证永久保留。')

    def recover_derived(self, approved):
        cid = approved['plan']['chapter_id']
        with self._write() as tree:
            plan, payloads = self._derived_plan(tree, cid)
            if plan != approved['plan'] or any(digest(tree.read(n)) != key for n, key in approved['observed'].items()):
                fail('派生修复预览后来源或设置发生变化。', 'REVISION_CONFLICT')
            if all(before == after for before, after in payloads.values()):
                self.core.clear_read_caches()
                return {'transaction_id': plan['transaction_id'], 'state': 'DERIVED_DONE', 'body_changed': False}
            job_id = digest(admin.encode(plan))[:32]
            root = EXT + '/Derived/' + plan['transaction_id']
            folder = root + '/' + job_id
            for before, after in payloads.values():
                for payload in (before, after):
                    self._immutable(tree, EXT + '/History/' + digest(payload) + '.json', payload, backup=True)
            self._immutable(tree, folder + '/PLAN.json', admin.encode(plan))
            pointer = self._record(transaction_id=plan['transaction_id'], chapter_id=cid,
                                   job_id=job_id, plan_sha=digest(admin.encode(plan)))
            pointer_raw = admin.encode(pointer)
            old = tree.maybe_read(root + '/CURRENT.json')
            if old != pointer_raw:
                p, q = self._controls(tree)
                if q['used'] + len(pointer_raw) + RESERVE * len(q['entries']) > HARD:
                    fail('派生登记达到容量限制。', 'RECOVERY_CAPACITY')
                q['used'] += len(pointer_raw)
                self._ledger(tree, 'Quota', q)
                tree.publish(root + '/CURRENT.json', pointer_raw, digest(old) if old else None)
            checkpoint('derived_intent')
            # A persisted plan is also the exact one-time author confirmation.
            for target, (before, after) in payloads.items():
                self._derived_sources(tree, cid)
                current = tree.read(target)
                if current == after:
                    continue
                if current != before:
                    fail('派生设置出现并发修改，未覆盖。', 'RECOVERY_CONFLICT')
                tree.publish(target, after, digest(before))
                checkpoint('derived_file')
            self._derived_sources(tree, cid)
            self._immutable(tree, folder + '/DONE.json', admin.encode(self._record(plan_sha=digest(admin.encode(plan)))))
            self.core.clear_read_caches()
            return {'transaction_id': plan['transaction_id'], 'state': 'DERIVED_DONE', 'body_changed': False}
