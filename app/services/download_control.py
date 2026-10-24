"""Cooperative cancellation scoped to one explicitly started download."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import importlib
import inspect
import os
import sys
import threading
import time
import uuid

# CPU tooling can import this as app.services; the running app uses services.
# Both must share the same registry and context when loaded in one process.
sys.modules.setdefault("app.services.download_control", sys.modules[__name__])
sys.modules.setdefault("services.download_control", sys.modules[__name__])


class DownloadCancelled(RuntimeError):
    """The owner requested cancellation; no retry or publication may follow."""


_current_download = ContextVar("maestro_current_download", default=None)
_controls = {}
_controls_lock = threading.Lock()
_TERMINAL = {"completed", "failed", "cancelled"}


class DownloadControl:
    def __init__(self, owner: str = "explicit"):
        self.download_id = uuid.uuid4().hex
        # "job" marks a transfer a queued generation started on its own: it is
        # cancelled with that job, while a download the user asked for in
        # Settings keeps running.
        self.owner = owner
        self._lock = threading.RLock()
        self._cancelled = threading.Event()
        self._status = "downloading"
        self._sealed = False
        self._native_transfers = 0
        self._active_transfers = 0
        self._responses = set()
        self._partial_paths = set()
        self.ended_at = None

    @property
    def cancel_requested(self):
        return self._cancelled.is_set()

    @property
    def transferring(self):
        """Is a transfer actually in flight right now?"""
        with self._lock:
            return self._active_transfers > 0

    @contextmanager
    def transfer(self):
        """Mark the span in which a cancelled control can interrupt bytes."""

        with self._lock:
            self._active_transfers += 1
        try:
            yield
        finally:
            with self._lock:
                self._active_transfers -= 1

    def check(self):
        if self.cancel_requested:
            raise DownloadCancelled("Download cancelled.")

    def snapshot(self):
        with self._lock:
            return {
                "download_id": self.download_id,
                "status": self._status,
                "cancellable": self._status == "downloading" and not self._sealed and not self._native_transfers,
            }

    def request_cancel(self):
        with self._lock:
            if self._status in _TERMINAL or self._status == "cancelling":
                return self.snapshot()
            if self._sealed or self._native_transfers:
                raise ValueError("This download is finishing an operation that cannot be cancelled. Try again when Cancel is available.")
            self._cancelled.set()
            self._status = "cancelling"
            responses = tuple(self._responses)
        # Closing a stalled requests response can wait for its read timeout.
        # Keep the API responsive; the worker confirms cancellation after its
        # read unwinds and its own temporary-file cleanup has completed.
        if responses:
            threading.Thread(target=self._close_responses, args=(responses,), daemon=True).start()
        return self.snapshot()

    @staticmethod
    def _close_responses(responses):
        for response in responses:
            try:
                response.close()
            except Exception:
                pass

    def close_responses(self):
        with self._lock:
            responses = tuple(self._responses)
            self._responses.clear()
        self._close_responses(responses)

    def track_partial(self, path):
        """Remember a half-written file so a cancelled job transfer can remove it.

        Hugging Face writes ``*.incomplete`` beside the target and resumes it on
        the next attempt. A download a job started for itself is one nobody will
        use once the job is gone, so its bytes are removed instead of quietly
        consuming the disk; a download the user asked for keeps its partial file
        so the next attempt resumes it.
        """

        if not path:
            return
        with self._lock:
            self._partial_paths.add(str(path))

    def discard_partials(self):
        with self._lock:
            paths = tuple(self._partial_paths)
            self._partial_paths.clear()
        for path in paths:
            try:
                os.unlink(path)
                print(f"[Downloads] Removed partial file after cancel: {path}")
            except FileNotFoundError:
                pass
            except OSError as exc:
                print(f"[Downloads] Could not remove partial file {path}: {exc}")

    def seal(self):
        """Commit to installing verified bytes; subsequent cancellation loses."""
        with self._lock:
            self.check()
            self._sealed = True

    @contextmanager
    def publication(self):
        # A cancellation and an atomic rename cannot both win this boundary.
        with self._lock:
            self.check()
            yield

    @contextmanager
    def native_transfer(self):
        # Keep oversized HF native transfers working; do not advertise a
        # Cancel action while an opaque native transfer cannot acknowledge it.
        with self._lock:
            self.check()
            self._native_transfers += 1
        try:
            yield
        finally:
            with self._lock:
                self._native_transfers -= 1

    def finish(self, status):
        with self._lock:
            self._status = "cancelled" if self.cancel_requested else status
            self.ended_at = time.time()
            return self.snapshot()

    def wrap_response(self, response):
        if getattr(response, "_maestro_download_control", None) is self:
            return response
        try:
            self.check()
        except DownloadCancelled:
            self._close_responses((response,))
            raise
        with self._lock:
            self._responses.add(response)
        response._maestro_download_control = self
        original_iter = response.iter_content

        @wraps(original_iter)
        def cancellable_content(*args, **kwargs):
            try:
                self.check()
                for chunk in original_iter(*args, **kwargs):
                    self.check()
                    yield chunk
                self.check()
            except Exception:
                # Convert a socket-close error to cancellation before HF's
                # network retry layer can silently restart the download.
                self.check()
                raise
            finally:
                with self._lock:
                    self._responses.discard(response)
                self._close_responses((response,))

        response.iter_content = cancellable_content
        return response

    def wrap_reader(self, response):
        """Bound urllib's blocking reads as well as its progress callbacks."""
        if getattr(response, "_maestro_download_control", None) is self:
            return response
        try:
            self.check()
        except DownloadCancelled:
            self._close_responses((response,))
            raise
        with self._lock:
            self._responses.add(response)
        response._maestro_download_control = self
        original_read = response.read

        @wraps(original_read)
        def read(*args, **kwargs):
            try:
                with self.transfer():
                    self.check()
                    result = original_read(*args, **kwargs)
                    self.check()
                    return result
            except Exception:
                self.check()
                raise

        response.read = read
        return response


def create_download_control(owner: str = "explicit"):
    control = DownloadControl(owner)
    now = time.time()
    with _controls_lock:
        for key, existing in tuple(_controls.items()):
            if existing.ended_at is not None and now - existing.ended_at > 3600:
                _controls.pop(key, None)
        _controls[control.download_id] = control
    return control


def find_download_control(download_id):
    with _controls_lock:
        return _controls.get(download_id)


def cancel_active_downloads(owner: str | None = None) -> int:
    """Cancel in-flight transfers, optionally only those owned by ``owner``.

    A queued job that is cancelled while it fetches a checkpoint must stop that
    fetch: otherwise the queue waits for a model nobody is going to use.
    """

    with _controls_lock:
        candidates = [
            control for control in _controls.values()
            if control.snapshot()["status"] == "downloading"
            and control.transferring
            and (owner is None or control.owner == owner)
        ]
    cancelled = 0
    for control in candidates:
        try:
            snapshot = control.request_cancel()
        except ValueError:
            continue
        if snapshot["status"] == "cancelling":
            cancelled += 1
    return cancelled


def current_download_control():
    return _current_download.get()


def check_download_cancelled():
    control = current_download_control()
    if control is not None:
        control.check()


def seal_download():
    control = current_download_control()
    if control is not None:
        control.seal()


@contextmanager
def download_scope(control):
    token = _current_download.set(control)
    try:
        control.check()
        yield control
    finally:
        control.close_responses()
        _current_download.reset(token)


@contextmanager
def job_download_scope():
    """Make every transfer a queued job starts cancellable and job-owned.

    A generation that switches to a model that is not on disk downloads it
    before the first frame. Without this scope that transfer had no owner: the
    banner could not offer Cancel, and cancelling the job left the download
    running while the queue waited for a model nobody would use.
    """

    if current_download_control() is not None:
        yield None
        return
    control = create_download_control(owner="job")
    token = _current_download.set(control)
    try:
        yield control
    finally:
        control.finish("completed")
        _current_download.reset(token)


@contextmanager
def download_publication():
    control = current_download_control()
    if control is None:
        yield
    else:
        with control.publication():
            yield


_http_hf_transfer = ContextVar("maestro_http_hf_transfer", default=False)


def install_huggingface_cancellation():
    """Use interruptible HTTP inside manual downloads and propagate HF pools."""
    import os
    import socket
    import tempfile
    import urllib.request
    original_urlopen = urllib.request.urlopen
    if not getattr(original_urlopen, "_maestro_cancellation_patched", False):
        @wraps(original_urlopen)
        def urlopen(*args, **kwargs):
            control = current_download_control()
            if control is None:
                return original_urlopen(*args, **kwargs)
            control.check()
            if len(args) < 3 and kwargs.get("timeout", socket._GLOBAL_DEFAULT_TIMEOUT) is socket._GLOBAL_DEFAULT_TIMEOUT:
                kwargs["timeout"] = 30
            response = original_urlopen(*args, **kwargs)
            return control.wrap_reader(response)

        urlopen._maestro_cancellation_patched = True
        urllib.request.urlopen = urlopen
    original_urlretrieve = urllib.request.urlretrieve
    if not getattr(original_urlretrieve, "_maestro_cancellation_patched", False):
        @wraps(original_urlretrieve)
        def urlretrieve(url, filename=None, reporthook=None, data=None):
            control = current_download_control()
            if control is None:
                return original_urlretrieve(url, filename, reporthook, data)
            control.check()

            def report(*args):
                control.check()
                if reporthook is not None:
                    reporthook(*args)

            # urllib normally writes straight to the final path. Stage
            # manual downloads so cancellation cannot leave a partial model
            # looking installed or overwrite an existing completed file.
            directory = os.path.dirname(os.path.abspath(filename)) if filename is not None else None
            descriptor, temporary = tempfile.mkstemp(prefix=".maestro-", suffix=".download", dir=directory)
            os.close(descriptor)
            keep_temporary = False
            try:
                with control.transfer():
                    _, headers = original_urlretrieve(url, temporary, report, data)
                with control.publication():
                    if filename is None:
                        keep_temporary = True
                        return temporary, headers
                    os.replace(temporary, filename)
                return filename, headers
            finally:
                if not keep_temporary:
                    try:
                        os.unlink(temporary)
                    except FileNotFoundError:
                        pass

        urlretrieve._maestro_cancellation_patched = True
        urllib.request.urlretrieve = urlretrieve
    try:
        module = importlib.import_module("huggingface_hub.file_download")
        snapshot = importlib.import_module("huggingface_hub._snapshot_download")
    except ImportError:
        return
    if getattr(module, "_maestro_cancellation_patched", False):
        return
    original_tmp = module._download_to_tmp_and_move
    original_xet_available = module.is_xet_available
    original_http_get = module.http_get
    original_thread_map = snapshot.thread_map
    tmp_signature = inspect.signature(original_tmp)

    @wraps(original_tmp)
    def download_to_tmp(*args, **kwargs):
        control = current_download_control()
        if control is None:
            return original_tmp(*args, **kwargs)
        with control.transfer():
            try:
                control.check()
                # Bind the installed helper's signature, which accepts
                # positional arguments in some supported HF versions.
                bound = tmp_signature.bind(*args, **kwargs)
                control.track_partial(bound.arguments.get("incomplete_path"))
                size = bound.arguments.get("expected_size", kwargs.get("expected_size"))
                limit = getattr(module.constants, "MAX_HTTP_DOWNLOAD_SIZE", 50 * 1024**3)
                if size is not None and size > limit:
                    with control.native_transfer():
                        return original_tmp(*args, **kwargs)
                token = _http_hf_transfer.set(True)
                try:
                    if "xet_file_data" in tmp_signature.parameters:
                        # Use the same signed URL through HTTP without falsely
                        # warning that the installed hf_xet package is missing.
                        bound.arguments["xet_file_data"] = None
                    result = original_tmp(*bound.args, **bound.kwargs)
                    control.check()
                    return result
                finally:
                    _http_hf_transfer.reset(token)
            except Exception as error:
                if control.cancel_requested:
                    # Closing the response makes the underlying read raise its
                    # own error, so the cancellation is what gets reported. A
                    # job's transfer is discarded because nobody wants that
                    # model; a download the user asked for keeps its partial
                    # file so the next attempt resumes it.
                    if control.owner == "job":
                        control.discard_partials()
                    raise DownloadCancelled("Download cancelled.") from error
                raise

    @wraps(original_xet_available)
    def xet_available():
        return False if _http_hf_transfer.get() else original_xet_available()

    @wraps(original_http_get)
    def http_get(*args, **kwargs):
        if _http_hf_transfer.get():
            check_download_cancelled()
            headers = dict(kwargs.get("headers") or {})
            if not any(str(key).lower() == "range" for key in headers):
                # HF's documented custom-range path also bypasses hf_transfer,
                # which otherwise delegates to an opaque native download.
                headers["Range"] = "bytes=0-"
            kwargs["headers"] = headers
        return original_http_get(*args, **kwargs)

    @wraps(original_thread_map)
    def thread_map(function, *iterables, **kwargs):
        control = current_download_control()
        if control is None:
            return original_thread_map(function, *iterables, **kwargs)

        @wraps(function)
        def scoped_function(*args, **kw):
            token = _current_download.set(control)
            try:
                control.check()
                result = function(*args, **kw)
                control.check()
                return result
            finally:
                _current_download.reset(token)

        return original_thread_map(scoped_function, *iterables, **kwargs)

    module._download_to_tmp_and_move = download_to_tmp
    module.is_xet_available = xet_available
    module.http_get = http_get
    snapshot.thread_map = thread_map
    module._maestro_cancellation_patched = True
