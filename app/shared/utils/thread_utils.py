# based on FramePack https://github.com/lllyasviel/FramePack

import traceback

from threading import Condition, Thread, Lock


class Listener:
    task_queue = []
    lock = Lock()
    thread = None
    # One long-lived dispatcher: waiting on a condition keeps an idle Maestro
    # from waking the scheduler 1000 times a second, which matters on a host
    # that is already short of memory.
    _has_task = Condition(lock)

    @classmethod
    def _process_tasks(cls):
        while True:
            with cls._has_task:
                while not cls.task_queue:
                    cls._has_task.wait()
                task = cls.task_queue.pop(0)
                
            func, args, kwargs, thread_name = task
            current_name = None
            try:
                if thread_name:
                    current_name = cls.thread.name
                    cls.thread.name = thread_name
                func(*args, **kwargs)
            except Exception as e:
                tb = traceback.format_exc().split('\n')[:-1] 
                print('\n'.join(tb))

                # print(f"Error in listener thread: {e}")
            finally:
                if current_name is not None:
                    cls.thread.name = current_name
    
    @classmethod
    def add_task(cls, func, *args, thread_name=None, **kwargs):
        with cls._has_task:
            cls.task_queue.append((func, args, kwargs, thread_name))
            cls._has_task.notify()

        if cls.thread is None:
            cls.thread = Thread(target=cls._process_tasks, daemon=True)
            cls.thread.start()


def async_run(func, *args, thread_name=None, **kwargs):
    Listener.add_task(func, *args, thread_name=thread_name, **kwargs)


class FIFOQueue:
    def __init__(self):
        self.queue = []
        self.lock = Lock()
        # Wake a waiting reader instead of polling: a stopped render used to
        # leave this spinning at 1 kHz, which both burns a core beside the
        # draining GPU work and can raise OSError 1450 on a memory-starved
        # Windows host.
        self._updated = Condition(self.lock)

    def push(self, cmd, data = None):
        with self._updated:
            self.queue.append( (cmd, data) )
            self._updated.notify_all()

    def pop(self):
        with self.lock:
            if self.queue:
                return self.queue.pop(0)
            return None

    def top(self):
        with self.lock:
            if self.queue:
                return self.queue[0]
            return None

    def next(self, timeout = None):
        """Pop the oldest event, waiting for one to arrive.

        ``timeout=None`` blocks until an event is pushed, preserving the
        original contract. With a timeout the wait returns ``None`` so a
        caller can re-check cancellation instead of blocking on a producer
        that is still shutting down.
        """
        with self._updated:
            if timeout is None:
                while not self.queue:
                    self._updated.wait()
            elif not self.queue:
                self._updated.wait(timeout)
            if not self.queue:
                return None
            return self.queue.pop(0)


class AsyncStream:
    def __init__(self):
        self.input_queue = FIFOQueue()
        self.output_queue = FIFOQueue()
