"""Saving finalized parts to TBL_TEST_RESULTS exactly once.

Recovery policy:
* Every finalized part (OK, NG, INCOMPLETE, ABORTED) is written to a backlog
  file first (<Saved to>/unsaved_results.json), so a crash or a database
  outage never loses it.
* Saving runs on a worker thread and is idempotent: rows carry the part's
  TR_PART_UID and db.insert_test_results() skips a part that is already in
  the table, so a retry after a lost acknowledgement cannot duplicate it.
* A part leaves the backlog only after the database confirmed it; the
  "saved" event for it is produced once. Production / OK / NG counters are
  updated on that event (and only for counted parts), so counters and saved
  records stay consistent.
* While the backlog is not empty the Test Console refuses RUN and retries
  periodically; the operator sees "NOT SAVED" with the database error.
"""

import json
import os
import queue
import threading


class ResultStore:
    def __init__(self, backlog_path, insert_rows, make_rows):
        self.path = backlog_path
        self.insert_rows = insert_rows          # rows -> count (idempotent per part uid)
        self.make_rows = make_rows              # record -> rows
        self.events = queue.Queue()             # ("saved", record) / ("failed", record, error)
        self._lock = threading.Lock()
        self._busy = False
        self._done = set()                      # part ids already reported as saved
        self.backlog = self._load()

    # ------------------------------------------------------------ backlog file
    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return []

    def _write(self):
        folder = os.path.dirname(self.path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.backlog, f, default=str)
        os.replace(tmp, self.path)

    def pending(self):
        with self._lock:
            return [r["part_id"] for r in self.backlog]

    # ------------------------------------------------------------ saving
    def submit(self, record):
        with self._lock:
            if record["part_id"] in self._done or any(r["part_id"] == record["part_id"] for r in self.backlog):
                return False                    # the same part submitted twice
            self.backlog.append(record)
            self._write()
        self.retry()
        return True

    def retry(self):
        with self._lock:
            if self._busy or not self.backlog:
                return
            self._busy = True
        threading.Thread(target=self._work, daemon=True).start()

    def save_now(self):
        """Synchronous variant (tests)."""
        with self._lock:
            self._busy = True
        self._work()

    def _work(self):
        try:
            while True:
                with self._lock:
                    if not self.backlog:
                        return
                    record = self.backlog[0]
                try:
                    self.insert_rows(self.make_rows(record))
                except Exception as exc:
                    self.events.put(("failed", record, exc))
                    return
                with self._lock:
                    self.backlog = [r for r in self.backlog if r["part_id"] != record["part_id"]]
                    first = record["part_id"] not in self._done
                    self._done.add(record["part_id"])
                    try:
                        self._write()
                    except OSError:
                        pass
                if first:
                    self.events.put(("saved", record))
        finally:
            with self._lock:
                self._busy = False
