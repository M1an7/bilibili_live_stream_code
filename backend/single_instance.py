from __future__ import annotations

import ctypes
import logging
import threading
import time
from ctypes import wintypes
from typing import Callable, Protocol


logger = logging.getLogger("SingleInstance")


class WindowsInstanceApi(Protocol):
    def create_mutex(self, name: str) -> tuple[object, bool]: ...
    def create_event(self, name: str) -> object: ...
    def open_event(self, name: str) -> object | None: ...
    def set_event(self, handle: object) -> bool: ...
    def wait_for_event(self, handle: object, timeout_ms: int) -> bool: ...
    def close_handle(self, handle: object) -> None: ...


class CtypesWindowsInstanceApi:
    ERROR_ALREADY_EXISTS = 183
    EVENT_MODIFY_STATE = 0x0002
    WAIT_OBJECT_0 = 0

    def __init__(self):
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateEventW.restype = wintypes.HANDLE
        kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.OpenEventW.restype = wintypes.HANDLE
        kernel32.SetEvent.argtypes = [wintypes.HANDLE]
        kernel32.SetEvent.restype = wintypes.BOOL
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        self.kernel32 = kernel32

    def create_mutex(self, name: str) -> tuple[object, bool]:
        ctypes.set_last_error(0)
        handle = self.kernel32.CreateMutexW(None, False, name)
        return handle, ctypes.get_last_error() == self.ERROR_ALREADY_EXISTS

    def create_event(self, name: str) -> object:
        return self.kernel32.CreateEventW(None, False, False, name)

    def open_event(self, name: str) -> object | None:
        return self.kernel32.OpenEventW(self.EVENT_MODIFY_STATE, False, name) or None

    def set_event(self, handle: object) -> bool:
        return bool(self.kernel32.SetEvent(handle))

    def wait_for_event(self, handle: object, timeout_ms: int) -> bool:
        return self.kernel32.WaitForSingleObject(handle, timeout_ms) == self.WAIT_OBJECT_0

    def close_handle(self, handle: object) -> None:
        self.kernel32.CloseHandle(handle)


class WindowsSingleInstance:
    MUTEX_NAME = r"Local\BiliLiveTool.SingleInstance.v1"
    EVENT_NAME = r"Local\BiliLiveTool.Activate.v1"

    def __init__(
        self,
        api: WindowsInstanceApi | None = None,
        notification_retries: int = 30,
        notification_delay: float = 0.1,
    ):
        self.api = api or CtypesWindowsInstanceApi()
        self.notification_retries = max(1, int(notification_retries))
        self.notification_delay = max(0.0, float(notification_delay))
        self._mutex_handle: object | None = None
        self._event_handle: object | None = None
        self._closed = threading.Event()
        self._listener: threading.Thread | None = None

    def acquire(self) -> bool:
        self._mutex_handle, already_exists = self.api.create_mutex(self.MUTEX_NAME)
        if not self._mutex_handle:
            raise OSError("unable to create the BiliLiveTool single-instance mutex")
        if already_exists:
            self._notify_existing_instance()
            self.close()
            return False
        self._event_handle = self.api.create_event(self.EVENT_NAME)
        if not self._event_handle:
            self.close()
            raise OSError("unable to create the BiliLiveTool activation event")
        return True

    def _notify_existing_instance(self) -> bool:
        for attempt in range(self.notification_retries):
            event_handle = self.api.open_event(self.EVENT_NAME)
            if event_handle:
                try:
                    return self.api.set_event(event_handle)
                finally:
                    self.api.close_handle(event_handle)
            if attempt + 1 < self.notification_retries:
                time.sleep(self.notification_delay)
        return False

    def start_activation_listener(self, callback: Callable[[], None]) -> None:
        if not self._event_handle or self._listener:
            return

        def listen() -> None:
            while not self._closed.is_set():
                if not self.api.wait_for_event(self._event_handle, 250):
                    continue
                try:
                    callback()
                except Exception:
                    logger.exception("Failed to activate the primary application window")

        self._listener = threading.Thread(
            target=listen,
            name="bililive-single-instance",
            daemon=True,
        )
        self._listener.start()

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        if self._listener and self._listener.is_alive():
            self._listener.join(timeout=1.0)
        for handle_name in ("_event_handle", "_mutex_handle"):
            handle = getattr(self, handle_name)
            if handle:
                self.api.close_handle(handle)
                setattr(self, handle_name, None)
