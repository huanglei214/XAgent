import unittest


class _FakeLoop:
    def __init__(self) -> None:
        self.callbacks = []
        self.stopped = False

    def call_soon_threadsafe(self, callback, *args) -> None:
        self.callbacks.append((callback, args))
        if callback.__name__ == "stop":
            self.stopped = True

    def stop(self) -> None:
        self.stopped = True


class _FakeThread:
    def __init__(self) -> None:
        self.join_calls = []

    def join(self, timeout=None) -> None:
        self.join_calls.append(timeout)


class SessionRuntimeManagerCloseTests(unittest.TestCase):
    def test_close_swallows_shutdown_timeout_and_stops_loop(self) -> None:
        from xagent.agent.runtime.manager import SessionRuntimeManager

        manager = SessionRuntimeManager(cwd=".", agent_factory=lambda: object(), runtime_factory=lambda *args, **kwargs: None)
        fake_loop = _FakeLoop()
        fake_thread = _FakeThread()
        manager._loop = fake_loop
        manager._thread = fake_thread
        manager._call = lambda coro: (_ for _ in ()).throw(RuntimeError("Runtime manager operation timed out."))

        manager.close()

        self.assertIsNone(manager._loop)
        self.assertIsNone(manager._thread)
        self.assertEqual(fake_thread.join_calls, [2])
        self.assertTrue(any(callback.__name__ == "_cancel_pending_tasks" for callback, _ in fake_loop.callbacks))
        self.assertTrue(any(callback.__name__ == "stop" for callback, _ in fake_loop.callbacks))
