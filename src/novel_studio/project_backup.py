"""Explicit local backup copy. No unpacking, sessions, network or source writes."""
import io
import json
import zipfile
import zlib
from pathlib import Path
from .core import CoreError, SafeTree, ROOT, admin, digest, relative_parts, TEXT_BUDGET_BYTES

MANIFEST = 'NOVEL-STUDIO-BACKUP.json'
FORMAT = 'novel-studio-backup-copy-v1'
LIMIT = 128 * 1024 * 1024
ENTRIES = 12000
LOCKS = {'.novel-studio/.ns-author.lock'}


def invalid():
    raise CoreError('BACKUP_INVALID', 'ZIP、文件清单或内容哈希未通过校验，备份操作未确认完成；请保留已有备份和暂存文件。')


def verify(payload, expected_files=None, expected_directories=None):
    """Validate our v1 copy before publication; never extract or write a file."""
    if len(payload) > LIMIT:
        invalid()
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            entries = archive.infolist()
            if len(entries) > ENTRIES + 1:
                invalid()
            files, directories, identities = {}, set(), set()
            total = 0
            for info in entries:
                name = info.filename
                clean = name[:-1] if info.is_dir() else name
                if not clean or '\\' in clean or '\x00' in clean or '/'.join(relative_parts(clean)) != clean:
                    invalid()
                identity = clean.casefold()
                if identity in identities:
                    invalid()
                identities.add(identity)
                kind = (info.external_attr >> 16) & 0o170000
                if kind not in ({0, 0o040000} if info.is_dir() else {0, 0o100000}):
                    invalid()
                if info.flag_bits & 1 or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                    invalid()
                if not 0 <= info.file_size <= TEXT_BUDGET_BYTES:
                    invalid()
                if info.is_dir():
                    if info.file_size or archive.read(info):
                        invalid()
                    directories.add(name)
                else:
                    total += info.file_size if name != MANIFEST else 0
                    if total > LIMIT:
                        invalid()
                    files[name] = info
            if MANIFEST not in files:
                invalid()
            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        invalid()
                    result[key] = value
                return result
            manifest = json.loads(archive.read(files.pop(MANIFEST)), object_pairs_hook=unique)
            if not isinstance(manifest, dict) or manifest.get('format') != FORMAT or not isinstance(manifest.get('files'), dict):
                invalid()
            hashes = manifest['files']
            if set(hashes) != set(files) or expected_files is not None and hashes != expected_files:
                invalid()
            if expected_directories is not None and directories != set(expected_directories):
                invalid()
            folded_files = {name.casefold() for name in [*files, MANIFEST]}
            for name in [*files, *(n[:-1] for n in directories)]:
                if any('/'.join(name.casefold().split('/')[:i]) in folded_files for i in range(1, len(name.split('/')))):
                    invalid()
            for name, info in files.items():
                expected = hashes[name]
                if not isinstance(expected, str) or len(expected) != 64 or any(c not in '0123456789abcdef' for c in expected):
                    invalid()
                if digest(archive.read(info)) != expected:
                    invalid()
            return {'files': hashes, 'directories': sorted(directories), 'format': FORMAT}
    except (ValueError, TypeError, OSError, RuntimeError, zipfile.BadZipFile, NotImplementedError, EOFError, zlib.error):
        invalid()


def inventory(tree):
    # Only this live lock is acquired by backup itself. Other .ns-* files can
    # be the sole durable B02 approval/recovery evidence and must be preserved.
    try:
        return [n for n in tree.files(None, file_limit=ENTRIES, include_directories=True) if n not in LOCKS]
    except ValueError as error:
        if str(error) == 'Snapshot file limit exceeded':
            raise CoreError('BACKUP_LIMIT', '工程文件和目录超过本版 12000 项备份清单保护；未截断或发布不完整副本。') from None
        raise


def backup(core, target):
    target = Path(target)
    if not target.is_absolute() or '..' in target.parts:
        raise CoreError('EXPORT_INVALID', '请选择完整的本机 ZIP 保存位置。')
    if target.suffix.lower() != '.zip' or any(target.is_relative_to(root) for root in (core.project, ROOT, core.plugin)):
        raise CoreError('EXPORT_PATH_DENIED', '请选择作品和安装目录之外的新 ZIP 文件。')
    with core.mutex:
        core.require_idle()
        with SafeTree(core.project) as tree, tree.lock():
            # Role registration uses the same lock. Recheck after acquiring it
            # to close the idle-check/role-start race without claiming role locks.
            core.require_idle()
            entries = inventory(tree)
            if len(entries) > ENTRIES:
                raise CoreError('BACKUP_LIMIT', '工程文件较多，超过本版完整备份的文件数量保护；没有生成不完整副本。')
            files = [n for n in entries if not n.endswith('/')]
            directories = [n for n in entries if n.endswith('/')]
            if any(n.rstrip('/').casefold() == MANIFEST.casefold() for n in entries):
                raise CoreError('BACKUP_INVALID', '工程内存在与备份清单同名的文件或目录，未忽略或覆盖它，请保留工程原状。')
            size, hashes, stream = 0, {}, io.BytesIO()
            with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
                for name in directories:
                    archive.writestr(name, b'')
                for name in files:
                    raw = tree.read(name)
                    size += len(raw)
                    if size > LIMIT:
                        raise CoreError('BACKUP_LIMIT', '工程超过本版 128 MiB 完整备份保护，没有截断或生成不完整副本。')
                    hashes[name] = digest(raw)
                    archive.writestr(name, raw)
                archive.writestr(MANIFEST, admin.encode({'format':FORMAT, 'files':hashes,
                    'note':'完整本机备份副本；包含作品和历史，不含本机安全凭据。原会话配置绑定原路径；此文件不是自动安装器或异名会话克隆。'}))
            payload = stream.getvalue()
            if len(payload) > LIMIT:
                raise CoreError('BACKUP_LIMIT', '压缩结果超过本机发布保护，未发布副本。')
            verify(payload, hashes, directories)
            expected_zip = digest(payload)
            def check_staged(staged):
                core.require_idle()
                if digest(staged) != expected_zip:
                    invalid()
                for name, expected in hashes.items():
                    if digest(tree.read(name)) != expected:
                        raise CoreError('REVISION_CONFLICT', '备份期间源文件发生变化，未发布副本。')
                if entries != inventory(tree):
                    raise CoreError('REVISION_CONFLICT', '备份期间文件或目录清单发生变化，未发布副本。')
                core.require_idle()
            with SafeTree(target.parent, limit=LIMIT) as output:
                if output.maybe_read(target.name) is not None:
                    raise CoreError('BACKUP_EXISTS', '同名备份已存在，请选择新的名称；不会覆盖。')
                try:
                    output.publish(target.name, payload, None, verify_staged=check_staged)
                except FileExistsError:
                    raise CoreError('BACKUP_EXISTS', '保存位置出现同名文件，请选择新的名称；不会覆盖。') from None
                except ValueError:
                    if output.maybe_read(target.name) is not None:
                        raise CoreError('BACKUP_EXISTS', '保存位置出现同名文件，请选择新的名称；不会覆盖。') from None
                    raise
                except OSError:
                    raise CoreError('BACKUP_IO', '备份操作未确认完成，可能是空间不足、权限限制或磁盘写入失败。请保留作品、已有备份和内部暂存文件。') from None
    return {'path':str(target), 'bytes':len(payload), 'files':len(files), 'directories':len(directories), 'sha256':expected_zip}
