import threading
import time
from unittest.mock import Mock, patch


def test_shutdown_returns_immediately_and_releases_api_lock():
    from scripts import arkloop_webview

    api = arkloop_webview.ArkLoopApi(Mock())
    backend_entered = threading.Event()
    backend_release = threading.Event()

    def stop_backend():
        with api._lock:
            backend_entered.set()
        backend_release.wait(timeout=1.0)

    api.backend = Mock(stop=stop_backend)

    with (
        patch.object(api, "stop_playback", return_value=None),
        patch.object(arkloop_webview, "get_ws_time_source") as get_ws,
    ):
        started_at = time.perf_counter()
        api._shutdown()
        elapsed = time.perf_counter() - started_at

        assert elapsed < 0.1
        assert backend_entered.wait(timeout=0.5)

        # Repeated native/frontend close events must not start another cleanup.
        shutdown_thread = api._shutdown_thread
        api._shutdown()
        assert api._shutdown_thread is shutdown_thread

        backend_release.set()
        assert api._wait_for_shutdown(timeout=1.0)
        get_ws.return_value.stop.assert_called_once_with()
