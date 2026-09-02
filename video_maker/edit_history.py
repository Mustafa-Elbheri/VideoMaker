from copy import deepcopy
from dataclasses import dataclass
from threading import RLock


@dataclass
class EditHistoryEntry:
    operation: str
    before: dict
    after: dict


class EditHistory:
    def __init__(self, limit=100):
        self.limit = max(1, int(limit))
        self._undo = []
        self._restore = []
        self._lock = RLock()

    def clear(self):
        with self._lock:
            self._undo.clear()
            self._restore.clear()

    def record(self, operation, before, after):
        if before == after:
            return False
        entry = EditHistoryEntry(str(operation), deepcopy(before), deepcopy(after))
        with self._lock:
            self._undo.append(entry)
            if len(self._undo) > self.limit:
                del self._undo[:len(self._undo) - self.limit]
            self._restore.clear()
        return True

    def undo(self):
        with self._lock:
            if not self._undo:
                return None
            entry = self._undo.pop()
            self._restore.append(entry)
            return entry.operation, deepcopy(entry.before)

    def restore(self):
        with self._lock:
            if not self._restore:
                return None
            entry = self._restore.pop()
            self._undo.append(entry)
            return entry.operation, deepcopy(entry.after)

    def can_undo(self):
        with self._lock:
            return bool(self._undo)

    def can_restore(self):
        with self._lock:
            return bool(self._restore)

    def undo_count(self):
        with self._lock:
            return len(self._undo)

    def restore_count(self):
        with self._lock:
            return len(self._restore)

    def next_undo_operation(self):
        with self._lock:
            return self._undo[-1].operation if self._undo else ""

    def next_restore_operation(self):
        with self._lock:
            return self._restore[-1].operation if self._restore else ""
