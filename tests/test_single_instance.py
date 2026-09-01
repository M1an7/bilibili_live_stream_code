from __future__ import annotations

import threading
import unittest

try:
    from backend.single_instance import WindowsSingleInstance
except ModuleNotFoundError:
    WindowsSingleInstance = None


class FakeWindowsInstanceApi:
    def __init__(self):
        self.mutex_exists = False
        self.activation_event = threading.Event()
        self.event_created = False
        self.next_handle = 10

    def create_mutex(self, name):
        handle = self.next_handle
        self.next_handle += 1
        already_exists = self.mutex_exists
        self.mutex_exists = True
        return handle, already_exists

    def create_event(self, name):
        self.event_created = True
        return 20

    def open_event(self, name):
        return 20 if self.event_created else None

    def set_event(self, handle):
        self.activation_event.set()
        return True

    def wait_for_event(self, handle, timeout_ms):
        signaled = self.activation_event.wait(min(timeout_ms / 1000, 0.01))
        if signaled:
            self.activation_event.clear()
        return signaled

    def close_handle(self, handle):
        pass


class WindowsSingleInstanceTests(unittest.TestCase):
    def test_second_launch_wakes_primary_and_does_not_become_an_app_instance(self):
        self.assertIsNotNone(
            WindowsSingleInstance,
            "Windows single-instance coordinator is not implemented",
        )
        api = FakeWindowsInstanceApi()
        primary = WindowsSingleInstance(api=api, notification_retries=1, notification_delay=0)
        secondary = WindowsSingleInstance(api=api, notification_retries=1, notification_delay=0)
        activated = threading.Event()

        try:
            self.assertTrue(primary.acquire())
            self.assertFalse(secondary.acquire())
            primary.start_activation_listener(activated.set)
            self.assertTrue(activated.wait(1), "primary instance did not receive the activation signal")
        finally:
            primary.close()
            secondary.close()


if __name__ == "__main__":
    unittest.main()
