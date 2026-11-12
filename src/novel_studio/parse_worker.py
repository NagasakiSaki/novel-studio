"""Short-lived bounded parser process. Reads stdin only; no file/command API."""
import ctypes
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
_job_handle = None


def limit_process():
    global _job_handle
    if os.name == "posix":
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
    elif os.name == "nt":
        from ctypes import wintypes as w
        class Basic(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong), ("flags", w.DWORD),
                        ("min_ws", ctypes.c_size_t), ("max_ws", ctypes.c_size_t), ("active", w.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]
        class Counters(ctypes.Structure):
            _fields_ = [("v" + str(i), ctypes.c_ulonglong) for i in range(6)]
        class Limits(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", Counters), ("process_memory", ctypes.c_size_t),
                        ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.restype = w.HANDLE
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        kernel.GetCurrentProcess.restype = w.HANDLE
        _job_handle = kernel.CreateJobObjectW(None, None)
        value = Limits()
        value.basic.flags = 0x100 | 0x8  # Process memory and child-process count.
        value.basic.active = 1
        value.process_memory = 512 * 1024 * 1024
        if not _job_handle or not kernel.SetInformationJobObject(_job_handle, 9, ctypes.byref(value), ctypes.sizeof(value)) or not kernel.AssignProcessToJobObject(_job_handle, kernel.GetCurrentProcess()):
            raise RuntimeError("Worker resource limits unavailable")
    else:
        raise RuntimeError("Unsupported resource-limited platform")


def main():
    try:
        limit_process()
        from novel_studio.material_formats import parse, FORMATS, SOURCE_LIMIT
        if len(sys.argv) != 2 or sys.argv[1] not in FORMATS:
            raise ValueError("Unsupported format")
        payload = sys.stdin.buffer.read(SOURCE_LIMIT + 1)
        result = {"ok": True, "data": parse(payload, sys.argv[1]), "resource_limit": "512MiB/25s_parent_timeout"}
    except Exception as error:
        result = {"ok": False, "error": {"code": getattr(error, "code", "PARSE_FAILED"),
                  "message": str(error) if hasattr(error, "code") else "文件损坏、结构不支持或解析资源受限，未导入；请核对原件或另存为 UTF-8 文本。"}}
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))


if __name__ == "__main__":
    main()
