"""Local file-backed store — the laptop equivalent of Aurora memory.

WHY THIS EXISTS (read this before changing anything):

    SCUDO learns from human review. When a reviewer approves or overrides a
    match, that decision is written back as a "precedent", and the NEXT time
    the same vendor product is matched the precedent is used directly instead
    of re-scoring. That is the whole learning loop.

    In AWS, precedents live in Aurora (see backend/scudo/aurora_memory.py).
    On a laptop with STORE_BACKEND=memory they live in a Python dict, which
    means they vanish the moment you stop the server -- so the loop is real
    but invisible: you can never restart and see that it remembered.

    This store fixes exactly that, and nothing else. It is MemoryStore (same
    scoring, same invariants) plus an append-only JSONL journal on disk.

HOW IT WORKS:

    - Every HITL decision is appended as one JSON line to
      $SCUDO_MEMORY_PATH (default: backend/local_memory/precedents.jsonl).
    - On startup the file is replayed through the SAME upsert_precedent()
      the live path uses. Replay is not a second implementation of the
      rules -- it is the same code, so the single-positive-precedent and
      negative-precedent invariants cannot drift between the two paths.
    - The file is human-readable. Open it in VS Code and you can literally
      see what the system has learned. Delete the file to make it forget.

Enable with STORE_BACKEND=local_file.
"""

from __future__ import annotations

import errno
import json
import os
import threading
import time
from pathlib import Path

from ..models import TaxonomyNode, VendorProductRef
from .memory_store import MemoryStore

# Directory fsync is not supported everywhere. These errnos mean "this
# filesystem does not do that" -- not "the write failed" -- and are the only
# ones tolerated below. EIO/ENOSPC and friends are real and must propagate.
#
# Both spellings of "not supported" are listed deliberately. On Linux ENOTSUP
# and EOPNOTSUPP are the same number and the frozenset simply dedupes them, but
# on macOS/BSD they are 45 and 102 -- so naming only one silently drops the
# other, and a mount that answers the unlisted spelling fails a HITL decision
# that this list exists to let through.
_FSYNC_UNSUPPORTED = frozenset(
    {
        errno.EINVAL,
        errno.EPERM,
        errno.EACCES,
        errno.ENOTSUP,
        errno.EOPNOTSUPP,
        errno.EISDIR,
    }
)

DEFAULT_MEMORY_PATH = (
    Path(__file__).resolve().parents[2] / "local_memory" / "precedents.jsonl"
)


def memory_path() -> Path:
    """Where the journal lives. Override with SCUDO_MEMORY_PATH."""
    raw = (os.getenv("SCUDO_MEMORY_PATH", "") or "").strip()
    return Path(raw) if raw else DEFAULT_MEMORY_PATH


class LocalFileStore(MemoryStore):
    """MemoryStore + a durable, human-readable precedent journal."""

    def __init__(self, path: Path | None = None) -> None:
        super().__init__()
        self._path = Path(path) if path is not None else memory_path()
        self._replaying = False
        # Flask serves requests on threads, so two reviewers can decide on the
        # same product at once. Without this lock the in-memory update and the
        # file append are separate steps: live state could end at B while the
        # file records B then A, and the next restart would replay A last and
        # silently REVERSE the decision. One lock covers both steps, so the
        # journal order always matches the order the memory saw.
        self._lock = threading.RLock()
        self._replay()

    # --- durability ----------------------------------------------------
    def _replay(self) -> None:
        """Rebuild in-memory precedents from the journal, oldest first."""
        if not self._path.exists():
            return
        self._replaying = True
        # Held for the whole rebuild so no live decision can interleave.
        try:
            with self._lock:
                # Split on bytes, decode per line. Two reasons, both of which
                # cost whole decisions if you read the file as text instead.
                #
                # First, a crash can tear the tail mid-CHARACTER, not just
                # mid-record: ensure_ascii=False writes "Carbón" as raw UTF-8,
                # so a half-written name can end on a lone lead byte. Decoding
                # the whole file up front raises before the first line is even
                # examined, so the store cannot be constructed and EVERY
                # earlier decision is unreachable -- the exact opposite of what
                # the skip-and-carry-on handler below is for. Per-line decoding
                # keeps the damage to the one torn line.
                #
                # Second, str.splitlines() breaks on U+2028, U+2029 and U+0085
                # as well as "\n", and json.dumps leaves those three raw when
                # ensure_ascii=False. A description pasted out of Word can thus
                # contain one, and the reader would split a single written
                # record into two unparseable halves and silently drop the
                # decision. Splitting on b"\n" makes the reader's definition of
                # a line exactly the writer's.
                for lineno, raw in enumerate(
                    self._path.read_bytes().split(b"\n"), start=1
                ):
                    if not raw.strip():
                        continue
                    try:
                        line = raw.decode("utf-8").strip()
                        rec = json.loads(line)
                        self.upsert_precedent(
                            ref=VendorProductRef(**rec["ref"]),
                            node=TaxonomyNode(**rec["node"]),
                            decision=rec["decision"],
                            decided_by=rec["decided_by"],
                            confidence=rec["confidence"],
                            provisional=rec.get("provisional", False),
                            decided_at_ms=rec.get("decided_at_ms"),
                        )
                    except Exception as exc:  # noqa: BLE001
                        # One bad line must not cost you every prior decision.
                        print(
                            f"[local_file_store] skipped {self._path}:{lineno}: {exc}",
                            flush=True,
                        )
        finally:
            self._replaying = False

    def upsert_precedent(
        self,
        *,
        ref,
        node,
        decision,
        decided_by,
        confidence,
        provisional=False,
        decided_at_ms=None,
    ):
        # Stamp REAL wall-clock time for a live decision. The inherited
        # FakeStore stamps a fake counter that starts at 2023-11-14, which is
        # fine for tests but would write a fictional date into an audit
        # journal a human is meant to read. Replay passes the recorded value
        # straight through, so a decision keeps its original timestamp
        # forever instead of being re-dated on every restart.
        if decided_at_ms is None and not self._replaying:
            decided_at_ms = int(time.time() * 1000)
        # Replay holds the lock for the whole file, so re-entering here is
        # normal -- hence RLock rather than Lock.
        with self._lock:
            self._write_locked(
                ref=ref,
                node=node,
                decision=decision,
                decided_by=decided_by,
                confidence=confidence,
                provisional=provisional,
                decided_at_ms=decided_at_ms,
            )

    def _write_locked(
        self,
        *,
        ref,
        node,
        decision,
        decided_by,
        confidence,
        provisional,
        decided_at_ms,
    ):
        """Append to the journal FIRST, then update memory -- one atomic unit.

        The order is deliberate. If memory were updated first and the append
        then failed (disk full, read-only mount), the running process would
        keep serving a decision no restart can recover: the reviewer sees
        "approved", the journal has no record of it, and the disagreement
        only surfaces when a restart silently reverses it. Journalling first
        fails the recoverable way -- the caller gets the OSError, memory is
        untouched, and the two can never disagree.
        """
        if self._replaying:
            # Replay must update memory but not re-append what it just read.
            return super().upsert_precedent(
                ref=ref,
                node=node,
                decision=decision,
                decided_by=decided_by,
                confidence=confidence,
                provisional=provisional,
                decided_at_ms=decided_at_ms,
            )
        rec = {
            "ref": {
                "vendor": ref.vendor,
                "product_id": ref.product_id,
                "name": ref.name,
                "description": ref.description,
                "source_content_hash": ref.source_content_hash,
                "source_file_audit_id": ref.source_file_audit_id,
            },
            "node": {"iri": node.iri, "label": node.label},
            "decision": decision,
            "decided_by": decided_by,
            "confidence": confidence,
            "provisional": provisional,
            "decided_at_ms": decided_at_ms,
            # Not read back on replay — purely so a human scanning the file can
            # see WHEN something was learned without converting epoch millis.
            "decided_at": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime((decided_at_ms or 0) / 1000)
            ),
        }
        # Empty, not merely absent. A first write that failed after opening the
        # file -- ENOSPC, the case this ordering exists for -- leaves a
        # zero-byte file behind, and latching on existence would let that
        # leftover claim the directory entry had already been flushed. The
        # retry that actually lands the bytes would then skip the flush for
        # good, so a power cut could lose the whole journal despite every
        # append being fsynced. An operator `touch` has the same effect.
        created = not self._path.exists() or self._path.stat().st_size == 0
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Where the journal ended before this record. If anything below fails
        # AFTER the append has landed, the file is truncated back to here so it
        # says exactly what the caller was told. Without that rollback the
        # directory fsync -- which runs after the record is already durable --
        # can raise, leaving a record on disk for a decision the caller was
        # told had failed: memory says no, the journal says yes, and the next
        # restart applies it. That is the mirror of the fail-open this write
        # ordering exists to prevent, and it is worse for an override, where
        # the phantom silently supersedes a decision that really was taken.
        offset = self._path.stat().st_size if self._path.exists() else 0
        # Exactly the bytes this writer intends to add, decided up front so the
        # rollback below can prove the tail is its own before removing it.
        intended = ""
        try:
            with self._path.open("a", encoding="utf-8") as fh:
                # Lead with a newline if a previous crash left a half-written
                # line with no terminator. Without this, the next append would
                # join onto that torn tail, fusing two records into one
                # unparseable line -- so the crash would cost the NEXT decision
                # as well as its own. A stray blank line is skipped by _replay
                # and costs nothing.
                if self._path.stat().st_size and self._needs_separator():
                    intended += "\n"
                # ensure_ascii=False keeps non-English vendor names readable.
                intended += json.dumps(rec, ensure_ascii=False) + "\n"
                fh.write(intended)
                fh.flush()
                os.fsync(fh.fileno())
            self._sync_parent_directory(created)
        except OSError:
            self._rollback(offset, intended.encode("utf-8"))
            raise
        # Durable on disk -- only now is it safe to act on.
        super().upsert_precedent(
            ref=ref,
            node=node,
            decision=decision,
            decided_by=decided_by,
            confidence=confidence,
            provisional=provisional,
            decided_at_ms=decided_at_ms,
        )

    def _rollback(self, offset: int, intended: bytes) -> None:
        """Remove this writer's own failed append -- and nothing else.

        The naive version of this (truncate straight back to `offset`) is
        WRONG, and dangerously so. self._lock is per-INSTANCE: it does not
        stop a second LocalFileStore on the same path, and there is no file
        lock here at all, so nothing stops a second OS process either. If
        another writer commits a record between the offset capture and this
        rollback, a blind truncate silently deletes THEIR decision -- one
        whose caller was told it succeeded and whose memory says approved.
        That is unrecoverable and undetectable, strictly worse than the
        visible phantom record this rollback exists to remove.

        So the truncate is conditional on proof of ownership: the file must
        still end with exactly the bytes we wrote, at exactly the offset we
        wrote them. If it does not -- someone else appended, or the append
        never landed at all -- the tail is not ours and we leave the file
        completely alone. Losing the phantom cleanup is the acceptable half
        of that trade; losing someone else's decision is not.
        """
        if not intended:
            # Nothing was written (the failure came before or during the
            # first write), so there is nothing of ours to remove.
            return
        try:
            if not self._path.exists():
                return
            size = self._path.stat().st_size
            if size != offset + len(intended):
                # Someone else has appended since, or our write landed short.
                # Either way the tail is not exclusively ours -- do not touch.
                return
            with self._path.open("rb") as fh:
                fh.seek(offset)
                if fh.read() != intended:
                    return
            os.truncate(self._path, offset)
        except OSError:
            # Best-effort: if the rollback itself fails there is nothing
            # better to do than report the original failure, which is the
            # one the caller needs to see.
            pass

    def _sync_parent_directory(self, created: bool) -> None:
        """Flush the journal's directory entry on the write that created it."""
        if created and os.name != "nt":
            # fsync above makes the CONTENTS durable, but on the very first
            # write the file's directory ENTRY may still be unflushed -- a
            # power cut can then lose the whole journal even though the bytes
            # were synced. Only needed when the file is newly created; later
            # appends do not touch the directory.
            #
            # Windows cannot open a directory handle at all, so it is skipped
            # outright rather than by catching the failure -- otherwise a real
            # I/O error and "this platform does not do that" look identical.
            #
            # On POSIX a REAL failure here (EIO, ENOSPC) is propagated: the
            # decision has not been made durable, and this file's whole
            # contract is that it never reports a decision it could not
            # persist. But "this filesystem does not support fsync on a
            # directory" is not a failure -- corporate VDI home directories are
            # often network mounts that answer EINVAL/EPERM, and turning that
            # into a failed HITL decision would break the feature for the
            # people it was written for.
            try:
                dir_fd = os.open(self._path.parent, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError as exc:
                if exc.errno not in _FSYNC_UNSUPPORTED:
                    raise

    def _needs_separator(self) -> bool:
        """True if this append must lead with "\\n" to avoid fusing records.

        Only called when the file is known to be non-empty -- the sole caller
        short-circuits on st_size, so the empty-file case (which cannot seek
        to -1) never reaches here and needs no handling.

        The interesting case is when the tail cannot be READ at all. There are
        three possible answers and only one is right:

          - Answer False (the original code's `except OSError: return True`
            equivalent): the guard is blind, so the next record welds onto a
            half-written one. The crash then costs the NEXT decision as well
            as its own, and the fused line never parses again. Unrecoverable.
          - Raise: safe for the file, but it fails a human's review decision
            over a condition that does not actually threaten durability. A
            journal that is writable but not readable -- mode 0o200, or a
            restrictive NFS/VDI ACL, the same corporate mounts the directory
            fsync tolerance above exists for -- would reject every decision
            even though the append itself would have succeeded.
          - Answer True, which is what this does. Writing the separator when
            the tail is unknown is safe in BOTH directions: on a torn tail it
            gives exactly the separation the guard is for, and on a
            well-terminated file it costs one blank line, which _replay
            already skips (see the comment at the call site). It achieves the
            full anti-fusion guarantee without failing anyone's decision.

        Errors are therefore swallowed here deliberately, and ONLY here -- a
        failing append or a failing contents fsync still propagates, because
        those genuinely mean the decision was not persisted.
        """
        try:
            with self._path.open("rb") as fh:
                fh.seek(-1, os.SEEK_END)
                return fh.read(1) != b"\n"
        except OSError:
            # Blind: assume torn. Costs a skipped blank line at worst.
            return True
