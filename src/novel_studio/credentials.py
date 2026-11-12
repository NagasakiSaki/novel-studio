"""Author-only Windows Credential Manager adapter; never enumerate credentials."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import re
import uuid
from .core import CoreError

PROVIDERS = frozenset({"openai", "anthropic", "gemini"})
REFERENCE = re.compile(r"NovelStudio:official:(openai|anthropic|gemini):[0-9a-f]{32}")


def validate_reference(reference, provider=None):
    if not isinstance(reference, str) or not REFERENCE.fullmatch(reference) or provider and reference.split(":")[2] != provider:
        raise CoreError("CREDENTIAL_SCOPE", "只能访问 Novel Studio 界面明确保存的供应商凭据。")
    return reference


class Credential(ctypes.Structure):
    _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)), ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]


class WindowsVault:
    def __init__(self):
        if os.name != "nt":
            raise CoreError("SECURE_STORE_UNAVAILABLE", "此版本的密钥界面需要 Windows 凭据管理器；不会回退到普通文件。")
        api = ctypes.WinDLL("advapi32", use_last_error=True)
        pointer = ctypes.POINTER(Credential)
        api.CredWriteW.argtypes = [pointer, wintypes.DWORD]
        api.CredWriteW.restype = wintypes.BOOL
        api.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(pointer)]
        api.CredReadW.restype = wintypes.BOOL
        api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        api.CredDeleteW.restype = wintypes.BOOL
        api.CredFree.argtypes = [ctypes.c_void_p]
        api.CredFree.restype = None
        self.api = api

    def save(self, provider, secret):
        if provider not in PROVIDERS or not isinstance(secret, str) or not secret or secret.strip() != secret or any(c.isspace() or ord(c) < 32 for c in secret):
            raise CoreError("INVALID_CREDENTIAL", "密钥不能为空或包含空白，请仅粘贴供应商提供的 API Key。")
        encoded = secret.encode("utf-8")
        if len(encoded) > 2560:
            raise CoreError("INVALID_CREDENTIAL", "密钥超出 Windows 安全存储限制。")
        reference = "NovelStudio:official:" + provider + ":" + uuid.uuid4().hex
        blob = (ctypes.c_ubyte * len(encoded)).from_buffer_copy(encoded)
        credential = Credential(Type=1, TargetName=reference, Comment="Novel Studio official API; author configured",
                                CredentialBlobSize=len(encoded), CredentialBlob=blob, Persist=2, UserName=provider)
        try:
            if not self.api.CredWriteW(ctypes.byref(credential), 0):
                raise CoreError("SECURE_STORE_FAILED", "Windows 未能保存密钥，没有写入配置文件。")
        finally:
            ctypes.memset(blob, 0, len(encoded))
        return reference

    def read(self, reference, provider):
        validate_reference(reference, provider)
        pointer = ctypes.POINTER(Credential)()
        if not self.api.CredReadW(reference, 1, 0, ctypes.byref(pointer)):
            raise CoreError("CREDENTIAL_MISSING", "此供应商的本机密钥不可用，请在模型管理中重新填写。")
        try:
            value = pointer.contents
            if value.TargetName != reference or value.Type != 1 or not 0 < value.CredentialBlobSize <= 2560:
                raise CoreError("CREDENTIAL_INVALID", "本机凭据格式不符合要求，请重新填写。")
            return ctypes.string_at(value.CredentialBlob, value.CredentialBlobSize).decode("utf-8")
        finally:
            self.api.CredFree(pointer)

    def delete(self, reference):
        validate_reference(reference)
        if not self.api.CredDeleteW(reference, 1, 0) and ctypes.get_last_error() != 1168:
            raise CoreError("SECURE_STORE_FAILED", "Windows 未能删除所选的 Novel Studio 凭据，请稍后重试。")
