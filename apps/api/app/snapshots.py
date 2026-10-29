"""Private, immutable capture envelopes; atomic completion precedes DB attachment."""
from datetime import datetime, timezone
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import stat
from time import monotonic, sleep
from uuid import UUID, uuid4

from .url_guard import FetchError, validate_url
from .url_runtime import MAX_RESULT_BYTES, validate_result


class SnapshotError(ValueError):
    def __init__(self):
        super().__init__("The source document is unavailable.")


def commit_file(temporary, path):
    # Windows rename fails if the destination exists; POSIX rename would replace it.
    if os.name == "nt":
        os.rename(temporary, path)
    else:
        os.link(temporary, path)


class SnapshotStore:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def path(self, owner_id, job_id, request_hash):
        if (str(UUID(owner_id)) != owner_id or str(UUID(job_id)) != job_id
                or not re.fullmatch(r"[a-f0-9]{64}", request_hash)):
            raise SnapshotError()
        path = self.root / owner_id / job_id / (request_hash + ".json")
        # Never follow links out of private storage or accept a linked completion file.
        if not path.resolve().is_relative_to(self.root) or any(p.is_symlink() for p in (path, path.parent, path.parent.parent)):
            raise SnapshotError()
        return path

    def safe_path(self, path):
        if not path.is_relative_to(self.root) or path.resolve() != path:
            raise SnapshotError()
        for part in (path, *path.parents):
            if part == self.root: break
            try: info = part.lstat()
            except FileNotFoundError: continue
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                raise SnapshotError()
        return path

    def marker(self, owner_id, job_id):
        self.path(owner_id, job_id, "0" * 64)  # Canonical IDs; no caller-controlled paths.
        return self.safe_path(self.root / ".deleted" / owner_id / job_id)

    @contextmanager
    def job_lock(self, owner_id, job_id):
        self.path(owner_id, job_id, "0" * 64)
        path = self.safe_path(self.root / ".locks" / owner_id / job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+b") as stream:
            if path.stat().st_size == 0:
                stream.write(b"0"); stream.flush()
            deadline = monotonic() + 2
            while True:
                try:
                    stream.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if monotonic() >= deadline: raise OSError("Snapshot storage is busy.") from None
                    sleep(.02)
            try: yield
            finally:
                stream.seek(0)
                if os.name == "nt": msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else: fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def validate(capture, url):
        try:
            validate_result(capture, validate_url(url), "capture")
            captured = datetime.fromisoformat(capture["retrieved_at"].replace("Z", "+00:00"))
            if (captured.tzinfo is None or capture["raw_representation"] != "decompressed_response_body"
                    or not isinstance(capture["content_type"], str) or len(capture["content_type"]) > 256):
                raise ValueError()
            return captured.astimezone(timezone.utc).replace(tzinfo=None)
        except (KeyError, TypeError, ValueError, OverflowError, FetchError):
            raise SnapshotError() from None

    def load(self, owner_id, job_id, request_hash, url):
        if self.marker(owner_id, job_id).exists(): raise SnapshotError()
        path = self.path(owner_id, job_id, request_hash)
        try:
            with path.open("rb") as stream:
                data = stream.read(MAX_RESULT_BYTES + 1)
        except FileNotFoundError:
            return None
        try:
            if len(data) > MAX_RESULT_BYTES: raise ValueError()
            envelope = json.loads(data)
            if envelope["identity"] != [owner_id, job_id, request_hash]: raise ValueError()
            capture = envelope["capture"]
            self.validate(capture, url)
            return capture
        except (KeyError, TypeError, ValueError):
            # A corrupt completed capture is never silently replaced by a new fetch.
            raise SnapshotError() from None

    def save(self, owner_id, job_id, request_hash, url, capture):
        with self.job_lock(owner_id, job_id):
            if self.marker(owner_id, job_id).exists(): raise SnapshotError()
            return self._save(owner_id, job_id, request_hash, url, capture)

    def _save(self, owner_id, job_id, request_hash, url, capture):
        self.validate(capture, url)
        path = self.path(owner_id, job_id, request_hash)
        data = json.dumps({"identity": [owner_id, job_id, request_hash], "capture": capture}, ensure_ascii=False).encode("utf-8")
        if len(data) > MAX_RESULT_BYTES: raise SnapshotError()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix("." + uuid4().hex + ".tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                # Create-if-absent: a concurrent/recovered worker cannot overwrite a winner.
                # The single envelope is the completion marker, including all raw/text bytes.
                commit_file(temporary, path)
            except FileExistsError:
                pass
            return self.load(owner_id, job_id, request_hash, url)
        finally:
            temporary.unlink(missing_ok=True)

    def relative_path(self, owner_id, job_id, request_hash):
        return self.path(owner_id, job_id, request_hash).relative_to(self.root).as_posix()

    def delete_job(self, owner_id, job_id, request_hash):
        """Permanently fence writers, then remove only this job's verified flat artifacts."""
        with self.job_lock(owner_id, job_id):
            directory = self.safe_path(self.path(owner_id, job_id, request_hash).parent)
            marker = self.marker(owner_id, job_id)
            marker.parent.mkdir(parents=True, exist_ok=True)
            try:
                with marker.open("xb") as stream:
                    stream.write(b"1"); stream.flush(); os.fsync(stream.fileno())
            except FileExistsError: pass
            if not directory.exists(): return
            for file in directory.iterdir():
                self.safe_path(file)
                if (not file.is_file() or not re.fullmatch(re.escape(request_hash) + r"(?:\.json|\.[a-f0-9]{32}\.tmp)", file.name)):
                    raise SnapshotError()
                file.unlink()
            directory.rmdir()  # Never recursively remove or follow a link.
