"""Own local compatibility receipts, protected by Windows DPAPI, not credentials."""
import ctypes
import json
import os
from pathlib import Path
from .core import CoreError, SafeTree, admin, digest


class Blob(ctypes.Structure):
    _fields_ = [('size', ctypes.c_ulong), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def protect(data, decrypt=False):
    if os.name != 'nt':
        raise CoreError('COMPAT_STORE_UNAVAILABLE', '当前平台没有已验证的兼容凭证保护；本地文件功能仍可使用。')
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(Blob)]
    function.restype = ctypes.c_int
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, target = Blob(len(data), buffer), Blob()
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise CoreError('COMPAT_STORE_INVALID', '兼容检查凭证无法验证；请重新检查，不会自动放行模型。')
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(target.data)


class ReceiptStore:
    def __init__(self, root, codec=protect):
        self.root, self.codec = Path(root), codec
        with SafeTree(self.root.parent) as parent:
            parent.mkdir(self.root.name)
        with SafeTree(self.root) as tree:
            tree.mkdir('.novel-studio')

    def read(self):
        with SafeTree(self.root) as tree:
            raw = tree.maybe_read('receipts.bin')
        if raw is None:
            return {'format': 'novel-compat-receipts-v1', 'records': {}}
        value = json.loads(self.codec(raw, True))
        if not isinstance(value, dict) or value.get('format') != 'novel-compat-receipts-v1' or not isinstance(value.get('records'), dict) or len(value['records']) > 64:
            raise CoreError('COMPAT_STORE_INVALID', '兼容检查凭证格式无法识别，请保留现有记录并重新检查。')
        return value

    def update(self, key, record):
        with SafeTree(self.root) as tree:
            with tree.lock():
                raw = tree.maybe_read('receipts.bin')
                value = self.read()
                value['records'][key] = record
                # Retain evidence; never delete earlier receipts to make room.
                if len(value['records']) > 64:
                    raise CoreError('COMPAT_HISTORY_FULL', '兼容记录已达保护上限；旧凭证保留，请维护人员归档。')
                tree.publish('receipts.bin', self.codec(admin.encode(value), False), digest(raw) if raw else None)
