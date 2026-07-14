"""Thread-backed execution state independent of the Flask presentation layer."""

import inspect
import logging
import threading
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple


logger = logging.getLogger(__name__)

TaskCallable = Callable[..., Any]
TaskArguments = Sequence[Any]


class TaskManager:
    """Serialize background jobs and expose their existing progress state."""

    def __init__(self) -> None:
        self.current_task: Optional[str] = None
        self.is_running = False
        self.message = "Idle"
        self.progress_detail = ""
        self.lock = threading.Lock()

    def start_task(
        self,
        task_func: TaskCallable,
        args: TaskArguments = (),
        task_name: str = "Task",
    ) -> Tuple[bool, str]:
        with self.lock:
            if self.is_running:
                return False, f"Task '{self.current_task}' is already running."

            self.is_running = True
            self.current_task = task_name
            self.message = f"Starting {task_name}..."
            self.progress_detail = ""

            thread = threading.Thread(
                target=self._run_wrapper,
                args=(task_func, args),
                daemon=True,
            )
            thread.start()
            return True, "Task started."

    def update_progress(self, data: Mapping[str, Any]) -> None:
        with self.lock:
            if "message" in data:
                self.message = data["message"]
            if "detail" in data:
                self.progress_detail = data["detail"]

    def _run_wrapper(self, func: TaskCallable, args: TaskArguments) -> None:
        try:
            signature = inspect.signature(func)
            if "progress_callback" in signature.parameters:
                func(*args, progress_callback=self.update_progress)
            else:
                func(*args)

            with self.lock:
                self.message = f"{self.current_task} completed successfully."
        except Exception as exc:
            logger.error("Task failed: %s", exc, exc_info=True)
            with self.lock:
                self.message = f"Error in {self.current_task}: {str(exc)}"
        finally:
            with self.lock:
                self.is_running = False
                self.current_task = None
