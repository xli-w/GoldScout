"""Best-effort, non-authoritative telemetry with no broker or ledger access."""

import json
import os
import tempfile
import threading
import uuid
from pathlib import Path
from queue import Empty, Full, Queue


class TelemetryPublisher:
    MAX_BYTES = 65536

    def __init__(self, path, database_id, environment, process_created_at):
        self.path = Path(path)
        self.database_id = database_id
        self.environment = environment
        self.process_created_at = process_created_at
        self.run_id = uuid.uuid4().hex
        self.failed = False
        self._queue = Queue(maxsize=1)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._work, name="dashboard-telemetry", daemon=True)
        self._thread.start()

    def submit(self, payload):
        try:
            envelope = dict(payload, version=1, database_id=self.database_id,
                            environment=self.environment, run_id=self.run_id,
                            pid=os.getpid(), process_created_at=self.process_created_at)
            encoded = json.dumps(envelope, allow_nan=False, separators=(",", ":")).encode("utf-8")
            if len(encoded) > self.MAX_BYTES:
                self.failed = True
                return False
            try:
                self._queue.put_nowait(encoded)
            except Full:
                try:
                    self._queue.get_nowait()
                except Empty:
                    pass
                self._queue.put_nowait(encoded)
            return True
        except Exception:
            self.failed = True
            return False

    def close(self):
        self._stop.set()

    def _work(self):
        while not self._stop.is_set() or not self._queue.empty():
            try:
                encoded = self._queue.get(timeout=0.1)
            except Empty:
                continue
            try:
                self._write(encoded)
                self.failed = False
            except Exception:
                self.failed = True

    def _write(self, encoded):
        descriptor, name = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(encoded)
            os.replace(name, self.path)
        finally:
            try:
                os.unlink(name)
            except FileNotFoundError:
                pass