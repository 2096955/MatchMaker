"""JPMC-LOCAL: the durable-memory store must actually survive a restart.

These tests exist because the whole point of LocalFileStore is the thing a
unit test of a dict cannot show: that a decision recorded by one process is
still there for the next one.
"""

from __future__ import annotations

import errno
import json
import os

import pytest

# Must come BEFORE the store imports below. `settings` is a frozen dataclass
# built once at first import of config.py, and STORE_BACKEND defaults to
# "falkordb" (config.py:219). These tests construct LocalFileStore directly, so
# they never needed the variable themselves -- but importing this module first
# froze settings on "falkordb", and a LATER test in the same pytest process
# that exercises the Flask app then tried to reach Redis on :6379 and failed
# with a confusing "Working outside of request context". The tests here all
# passed; the damage landed on someone else's test. Same two lines every other
# test module in this directory sets, for the same reason.
os.environ.setdefault("STORE_BACKEND", "memory")
os.environ.setdefault("FRAME_SOURCE", "mock")

from scudo_mapping_mcp.models import TaxonomyNode, VendorProductRef  # noqa: E402
from scudo_mapping_mcp.store.local_file_store import LocalFileStore  # noqa: E402

NODE = TaxonomyNode(iri="jpmorgan:data:cdao:subdomain:pricing", label="Pricing")
REF = VendorProductRef(vendor="LSEG", product_id="LSEG-CARBON-029", name="Carbon Data")

# The directory fsync is skipped outright when os.name == "nt" (see the comment
# at the guard in local_file_store._write_locked), because Windows cannot open a
# directory handle at all. Every test below that patches os.fsync to fail on a
# directory therefore has nothing to observe there: the must-raise ones fail
# outright, and -- worse -- the tolerated ones PASS while exercising nothing,
# which is a green test claiming coverage it does not have. Skipping says so out
# loud. Windows is a supported target for the local runner, so this is not
# hypothetical. If the guard in the store is ever removed, remove this too.
_needs_directory_fsync = pytest.mark.skipif(
    os.name == "nt",
    reason="directory fsync is skipped outright on Windows, so a patched "
    "os.fsync is never reached for a directory and the test proves nothing",
)


def _approve(store, node=NODE, ref=REF, confidence=0.95):
    store.upsert_precedent(
        ref=ref,
        node=node,
        decision="approve",
        decided_by="demo@local",
        confidence=confidence,
    )


def test_precedent_survives_a_new_store_instance(tmp_path):
    """The core promise: stop the process, start it again, still remembered."""
    path = tmp_path / "precedents.jsonl"
    _approve(LocalFileStore(path=path))

    reopened = LocalFileStore(path=path)  # simulates a restart
    got = reopened.get_precedent_mapping("LSEG", "LSEG-CARBON-029")
    assert got is not None
    assert got.mapped_node_iri == NODE.iri
    assert got.confidence == pytest.approx(0.95)


def test_journal_is_human_readable_one_line_per_decision(tmp_path):
    path = tmp_path / "precedents.jsonl"
    _approve(LocalFileStore(path=path))

    lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["decision"] == "approve"
    assert rec["decided_by"] == "demo@local"
    assert rec["node"]["label"] == "Pricing"


def test_replay_does_not_duplicate_the_journal(tmp_path):
    """Reopening must not re-append what it just read, or the file grows
    without bound on every restart."""
    path = tmp_path / "precedents.jsonl"
    _approve(LocalFileStore(path=path))
    before = path.read_text()

    LocalFileStore(path=path)
    LocalFileStore(path=path)
    assert path.read_text() == before


def test_reject_survives_restart_as_a_negative(tmp_path):
    path = tmp_path / "precedents.jsonl"
    store = LocalFileStore(path=path)
    store.upsert_precedent(
        ref=REF,
        node=NODE,
        decision="reject",
        decided_by="demo@local",
        confidence=0.0,
    )
    reopened = LocalFileStore(path=path)
    assert NODE.iri in reopened.get_negative_precedents("LSEG", "LSEG-CARBON-029")


def test_single_positive_precedent_invariant_holds_across_restart(tmp_path):
    """An override after an approve must leave exactly ONE positive precedent,
    replayed in order -- the invariant cannot drift between live and replay
    because replay calls the same upsert_precedent()."""
    path = tmp_path / "precedents.jsonl"
    store = LocalFileStore(path=path)
    _approve(store)
    other = TaxonomyNode(
        iri="jpmorgan:data:cdao:subdomain:reference", label="Reference"
    )
    store.upsert_precedent(
        ref=REF,
        node=other,
        decision="override",
        decided_by="demo@local",
        confidence=1.0,
    )

    reopened = LocalFileStore(path=path)
    got = reopened.get_precedent_mapping("LSEG", "LSEG-CARBON-029")
    assert got.mapped_node_iri == other.iri  # the later override wins


def test_a_corrupt_line_does_not_lose_the_other_decisions(tmp_path):
    path = tmp_path / "precedents.jsonl"
    _approve(LocalFileStore(path=path))
    with path.open("a") as fh:
        fh.write("{ this is not valid json\n")

    reopened = LocalFileStore(path=path)  # must not raise
    assert reopened.get_precedent_mapping("LSEG", "LSEG-CARBON-029") is not None


def test_decision_keeps_its_original_timestamp_across_restart(tmp_path):
    """A precedent must not be re-dated every time the server restarts, or the
    audit trail says everything was decided at the last boot. Also guards
    against the inherited FakeStore counter, which starts in 2023."""
    import time

    path = tmp_path / "precedents.jsonl"
    store = LocalFileStore(path=path)
    _approve(store)
    original = store.list_confirmed_precedents()[0]["decided_at_ms"]

    # A real wall-clock stamp, not the 1_700_000_00x fake counter.
    assert original > 1_600_000_000_000
    assert abs(original - int(time.time() * 1000)) < 60_000

    reopened = LocalFileStore(path=path)
    assert reopened.list_confirmed_precedents()[0]["decided_at_ms"] == original


def test_a_provisional_decision_stays_provisional_across_a_restart(tmp_path):
    """An unattended auto-map must not be promoted to a human precedent by a
    restart.

    Provisional results are deliberately hidden from get_precedent_mapping so
    an auto_mapped run never short-circuits a later call (I5). That flag is
    journalled, but nothing pinned it coming back: drop it on the way out and
    every provisional match returns after the next restart as a confirmed
    precedent, permanently skipping the HITL review it was waiting for."""
    path = tmp_path / "precedents.jsonl"
    store = LocalFileStore(path=path)
    store.upsert_precedent(
        ref=REF,
        node=NODE,
        decision="approve",
        decided_by="auto",
        confidence=0.95,
        provisional=True,
    )
    assert store.get_precedent_mapping("LSEG", "LSEG-CARBON-029") is None

    reopened = LocalFileStore(path=path)
    assert reopened.get_precedent_mapping("LSEG", "LSEG-CARBON-029") is None
    assert reopened.list_confirmed_precedents() == []


def test_the_source_audit_fields_survive_a_restart(tmp_path):
    """A decision must stay traceable to the exact landed file it was made on.

    The M8 federated-audit fields are written to the journal, but both default
    to None on VendorProductRef, so dropping them from the record replays
    without error and the link to the source file is lost silently -- every
    precedent still there, just no longer traceable."""
    path = tmp_path / "precedents.jsonl"
    ref = VendorProductRef(
        vendor="LSEG",
        product_id="LSEG-AUDIT-001",
        name="Audited",
        source_content_hash="deadbeef",
        source_file_audit_id="audit-7",
    )
    _approve(LocalFileStore(path=path), ref=ref)

    replayed = LocalFileStore(path=path).list_confirmed_precedents()[0]
    assert replayed["source_content_hash"] == "deadbeef"
    assert replayed["source_file_audit_id"] == "audit-7"


def test_a_recorded_timestamp_is_replayed_verbatim(tmp_path):
    """The across-restart half of the timestamp guard, pinned deterministically.

    Comparing a live stamp before and after a restart only catches re-dating
    when the two happen to straddle a millisecond boundary, so a store that
    re-dated every precedent on every replay passed that check most of the
    time. Seeding a stamp no wall clock can produce makes the check exact."""
    path = tmp_path / "precedents.jsonl"
    _approve(LocalFileStore(path=path))

    old_stamp = 1_600_000_100_000  # 2020, and not a value time.time() can return now
    rec = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    rec["decided_at_ms"] = old_stamp
    path.write_text(json.dumps(rec) + "\n", encoding="utf-8")

    reopened = LocalFileStore(path=path)
    assert reopened.list_confirmed_precedents()[0]["decided_at_ms"] == old_stamp


def test_non_ascii_names_stay_readable_in_the_journal(tmp_path):
    path = tmp_path / "precedents.jsonl"
    ref = VendorProductRef(vendor="LSEG", product_id="P1", name="Carbón Ünicode")
    _approve(LocalFileStore(path=path), ref=ref)
    assert "Carbón Ünicode" in path.read_text(encoding="utf-8")


def test_a_torn_last_line_does_not_swallow_the_next_decision(tmp_path):
    """A crash mid-write leaves a line with no terminator. The NEXT append must
    not fuse onto it -- otherwise one crash costs two decisions, and the second
    one was never at risk."""
    path = tmp_path / "precedents.jsonl"
    _approve(LocalFileStore(path=path))
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"ref": {"vendor": "LSEG", "produ')  # crash: no newline

    store = LocalFileStore(path=path)
    later = VendorProductRef(vendor="LSEG", product_id="LSEG-LATER-001", name="Later")
    _approve(store, ref=later)

    reopened = LocalFileStore(path=path)
    assert reopened.get_precedent_mapping("LSEG", "LSEG-LATER-001") is not None
    assert reopened.get_precedent_mapping("LSEG", "LSEG-CARBON-029") is not None


def test_a_crash_mid_utf8_character_does_not_cost_every_prior_decision(tmp_path):
    """A torn tail must cost its own line and nothing else -- including when
    the tear lands in the MIDDLE of a multi-byte character.

    ensure_ascii=False (which keeps non-English vendor names readable) means a
    name like "Carbón" is written as raw UTF-8, so a crash can leave a lone
    lead byte at the end of the file. Decoding the WHOLE file before splitting
    it turns that into a UnicodeDecodeError raised before the first line is
    even looked at -- so the store cannot be constructed at all, every earlier
    decision is unreachable, and because the app builds the store per request
    it keeps failing until a human hex-edits the journal. Decoding per line
    keeps the blast radius at the one torn line, which is what the skip-and-
    carry-on handler in _replay was written to provide."""
    path = tmp_path / "precedents.jsonl"
    store = LocalFileStore(path=path)
    for i in range(3):
        _approve(
            store, ref=VendorProductRef(vendor="LSEG", product_id=f"P{i}", name="ok")
        )
    with path.open("ab") as fh:
        # Crash mid-write, mid-character: a lone UTF-8 lead byte, no newline.
        fh.write(b'{"ref": {"vendor": "LSEG", "name": "Carb\xc3')

    reopened = LocalFileStore(path=path)  # must not raise
    for i in range(3):
        assert reopened.get_precedent_mapping("LSEG", f"P{i}") is not None


@pytest.mark.parametrize(
    "separator",
    [" ", " ", ""],
    ids=["u2028-line-separator", "u2029-paragraph-separator", "u0085-next-line"],
)
def test_a_unicode_line_separator_in_a_description_survives_a_restart(
    tmp_path, separator
):
    """The reader's idea of "a line" must match the writer's.

    json.dumps escapes the ASCII control characters but leaves U+2028, U+2029
    and U+0085 raw when ensure_ascii=False, and a reviewer pasting a
    description out of Word or a web page can easily carry one in. The writer
    emits one record per b"\\n"; str.splitlines() also breaks on those three
    code points, so one record is read back as two halves, both fail to parse,
    and both are quietly skipped. The reviewer approved it, the audit file
    visibly contains it, and the restart silently un-approves it -- exactly the
    live/journal divergence the write ordering exists to make impossible."""
    path = tmp_path / "precedents.jsonl"
    store = LocalFileStore(path=path)
    ref = VendorProductRef(
        vendor="LSEG",
        product_id="LSEG-PASTED-001",
        name="Pasted",
        description=f"first half{separator}second half",
    )
    _approve(store, ref=ref)

    # One physical record was written...
    assert path.read_bytes().count(b"\n") == 1
    # ...so exactly one must come back.
    assert store.get_precedent_mapping("LSEG", "LSEG-PASTED-001") is not None
    reopened = LocalFileStore(path=path)
    assert reopened.get_precedent_mapping("LSEG", "LSEG-PASTED-001") is not None


def test_an_unreadable_journal_still_fails_loudly(tmp_path):
    """Tolerating a torn line must not slide into tolerating an unreadable
    file.

    A journal that exists but cannot be read is not "no precedents yet" -- it
    is a store that would silently start empty and re-decide everything a
    human already decided. That must fail at construction, not degrade into a
    clean slate."""
    path = tmp_path / "precedents.jsonl"
    _approve(LocalFileStore(path=path))
    path.chmod(0o000)
    try:
        if os.access(path, os.R_OK):  # running as root: the chmod means nothing
            pytest.skip("cannot make a file unreadable as root")
        with pytest.raises(OSError):
            LocalFileStore(path=path)
    finally:
        path.chmod(0o600)


def test_a_failed_journal_write_does_not_leave_a_live_precedent(tmp_path):
    """If the decision cannot be made durable, it must not appear to have been
    taken.

    Memory-then-file loses the decision on the next restart while the running
    process keeps acting on it -- the reviewer sees "approved", the audit file
    has nothing, and the disagreement only surfaces later. File-then-memory
    fails the other way, which is recoverable: the caller gets an error and
    replay picks the record up. Disk full and a read-only mount both land
    here."""
    path = tmp_path / "blocked" / "precedents.jsonl"
    (tmp_path / "blocked").write_text("a file where the directory should be")

    store = LocalFileStore(path=path)
    with pytest.raises(OSError):
        _approve(store)

    assert store.get_precedent_mapping("LSEG", "LSEG-CARBON-029") is None


def test_concurrent_decisions_agree_between_memory_and_the_journal(tmp_path):
    """Flask serves on threads. Whatever the live store ends up believing, a
    restart must reach the SAME answer -- the journal order cannot disagree
    with the order memory saw, or a restart silently reverses a decision."""
    import threading

    path = tmp_path / "precedents.jsonl"
    store = LocalFileStore(path=path)
    nodes = [
        TaxonomyNode(iri=f"jpmorgan:data:cdao:subdomain:n{i}", label=f"N{i}")
        for i in range(12)
    ]

    barrier = threading.Barrier(len(nodes))

    def decide(node):
        barrier.wait()  # maximise the overlap
        store.upsert_precedent(
            ref=REF,
            node=node,
            decision="override",
            decided_by="demo@local",
            confidence=1.0,
        )

    threads = [threading.Thread(target=decide, args=(n,)) for n in nodes]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    live = store.get_precedent_mapping("LSEG", "LSEG-CARBON-029")
    replayed = LocalFileStore(path=path).get_precedent_mapping(
        "LSEG", "LSEG-CARBON-029"
    )
    assert replayed.mapped_node_iri == live.mapped_node_iri


@_needs_directory_fsync
@pytest.mark.parametrize("failure_errno", [errno.EIO, errno.ENOSPC])
def test_a_real_directory_fsync_failure_is_not_swallowed(
    tmp_path, monkeypatch, failure_errno
):
    """A genuine I/O error while making the journal durable must fail the
    decision, not be reported as success.

    The original code caught every OSError from the directory fsync, so
    ENOSPC/EIO -- the exact cases the fsync exists to catch -- were silently
    ignored and memory was updated anyway. That is the same fail-open bug the
    write-ordering fix was written to prevent, just one line further down.
    Both errnos are exercised because the docstring above claims both, and an
    allow-list that quietly grew to include ENOSPC would otherwise still be
    green."""
    real_fsync = os.fsync
    path = tmp_path / "precedents.jsonl"

    def fsync_fails_on_directories(fd):
        if os.fstat(fd).st_mode & 0o040000:  # S_IFDIR
            raise OSError(failure_errno, "simulated disk error")
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fsync_fails_on_directories)
    store = LocalFileStore(path=path)
    with pytest.raises(OSError):
        _approve(store)
    assert store.get_precedent_mapping("LSEG", "LSEG-CARBON-029") is None


@_needs_directory_fsync
def test_a_failed_decision_does_not_survive_as_a_phantom_record(tmp_path, monkeypatch):
    """A decision reported as FAILED must not come back on the next restart.

    The record is appended and fsynced before the directory is synced, so a
    genuine failure at the directory step raises AFTER the bytes are already
    durable. Memory is correctly left untouched -- but the journal is not, and
    replay applies what the reviewer was told did not happen. The mirror image
    of the fail-open this file was written to prevent: there the memory had a
    decision the journal did not, here the journal has one the caller denies.
    Worse in the override case, where a phantom silently supersedes a decision
    that really was taken. The append has to be rolled back so the file matches
    what the caller was told."""
    real_fsync = os.fsync
    path = tmp_path / "precedents.jsonl"

    def fsync_fails_on_directories(fd):
        if os.fstat(fd).st_mode & 0o040000:
            raise OSError(errno.EIO, "simulated disk error")
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fsync_fails_on_directories)
    store = LocalFileStore(path=path)
    with pytest.raises(OSError):
        _approve(store)
    monkeypatch.undo()

    assert store.get_precedent_mapping("LSEG", "LSEG-CARBON-029") is None
    # ...and a restart must agree with what the caller was told.
    reopened = LocalFileStore(path=path)
    assert reopened.get_precedent_mapping("LSEG", "LSEG-CARBON-029") is None


@_needs_directory_fsync
def test_an_empty_journal_still_gets_its_directory_entry_flushed(tmp_path, monkeypatch):
    """Existence is not proof the directory entry was ever synced.

    A first write that fails after the file is opened -- ENOSPC, the very
    condition this ordering exists for -- leaves a zero-byte file behind. If
    the "do I need to sync the directory?" test is "does the file exist?",
    that leftover permanently answers yes-it-is-already-done, and the retry
    that actually lands the bytes skips the flush. From then on every append
    syncs its contents while the directory entry stays unflushed, so a power
    cut can lose the entire journal -- exactly what the flush was added to
    prevent, and exactly on the network-mounted home directories it was added
    for. The same applies to any journal that exists at size zero for a duller
    reason: an operator touch, an editor writing an empty file."""
    import stat as _stat

    real_fsync = os.fsync
    path = tmp_path / "precedents.jsonl"
    path.write_bytes(b"")  # exists, but nothing has ever been written to it
    dir_fsyncs = []

    def counting_fsync(fd):
        if _stat.S_ISDIR(os.fstat(fd).st_mode):
            dir_fsyncs.append(fd)
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", counting_fsync)
    _approve(LocalFileStore(path=path))
    monkeypatch.undo()

    assert dir_fsyncs, (
        "the write that first put bytes in the journal did not flush the "
        "directory entry, so a power cut can lose the whole file"
    )


def test_the_journal_contents_are_actually_fsynced(tmp_path, monkeypatch):
    """The file-contents fsync is the store's primary durability call, and it
    was the one with no test at all.

    Both directory-fsync tests above deliberately let the FILE descriptor
    through to the real fsync, so deleting os.fsync(fh.fileno()) entirely --
    leaving only a flush into the page cache, which a power cut discards --
    left the whole suite green. This fails the file fsync specifically, which
    no other test touches."""
    import stat as _stat

    real_fsync = os.fsync
    path = tmp_path / "precedents.jsonl"
    seen = []

    def fsync_fails_on_regular_files(fd):
        if not _stat.S_ISDIR(os.fstat(fd).st_mode):
            seen.append(fd)
            raise OSError(errno.EIO, "simulated disk error syncing the record")
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fsync_fails_on_regular_files)
    store = LocalFileStore(path=path)
    with pytest.raises(OSError):
        _approve(store)
    monkeypatch.undo()

    assert seen, "the record was never fsynced -- a flush alone is not durable"
    assert store.get_precedent_mapping("LSEG", "LSEG-CARBON-029") is None


@_needs_directory_fsync
@pytest.mark.parametrize(
    "unsupported_errno",
    [errno.EINVAL, errno.EPERM, errno.EACCES, errno.ENOTSUP, errno.EOPNOTSUPP],
)
def test_an_unsupported_directory_fsync_is_tolerated(
    tmp_path, monkeypatch, unsupported_errno
):
    """...but "this filesystem cannot fsync a directory" is not a failure.

    Corporate VDI home directories are frequently network mounts that answer
    EINVAL. Treating that as a failed decision would break the feature for
    exactly the people it was written for, so it is tolerated -- the file
    contents were already fsynced successfully.

    Every spelling is exercised, because they are not interchangeable
    everywhere: on Linux ENOTSUP and EOPNOTSUPP are the same number, but on
    macOS/BSD they are 45 and 102, so listing only one silently drops the
    other and a mount answering the unlisted spelling fails the decision.
    EACCES/EPERM are reachable from the os.open of the directory rather than
    from the fsync itself, which is why the tolerance spans both calls."""
    real_fsync = os.fsync
    path = tmp_path / "precedents.jsonl"

    def fsync_unsupported_on_directories(fd):
        if os.fstat(fd).st_mode & 0o040000:
            raise OSError(unsupported_errno, "directory fsync not supported here")
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fsync_unsupported_on_directories)
    store = LocalFileStore(path=path)
    _approve(store)  # must NOT raise
    assert store.get_precedent_mapping("LSEG", "LSEG-CARBON-029") is not None


@pytest.mark.skipif(
    os.name == "nt",
    reason="the directory fsync is skipped outright on Windows, and POSIX "
    "directory permissions do not apply there",
)
@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root bypasses the directory permission bits this test relies on",
)
def test_a_directory_that_cannot_be_opened_still_completes_the_decision(tmp_path):
    """The tolerance deliberately spans the os.open of the directory, not just
    the fsync of it.

    A home directory that is writable but not readable (0o300 -- the shape
    some locked-down VDI images ship) fails at the OPEN, with EACCES, which
    fsync(2) itself can never return. By that point the record has already
    been written AND fsynced, so the decision is durable; only the directory
    ENTRY may be unflushed. Raising here would tell the reviewer the decision
    failed while a restart replays it anyway -- the same memory/journal
    disagreement the write ordering exists to prevent, merely inverted. This
    test exists so that narrowing the try to cover only the fsync call, which
    looks like a tightening, is recognised as the regression it is."""
    home = tmp_path / "vdi_home"
    home.mkdir()
    path = home / "precedents.jsonl"
    os.chmod(home, 0o300)  # writable + searchable, NOT readable
    try:
        store = LocalFileStore(path=path)
        _approve(store)  # must NOT raise
        assert store.get_precedent_mapping("LSEG", "LSEG-CARBON-029") is not None
    finally:
        os.chmod(home, 0o700)
    # ...and the decision it reported as taken really is on disk.
    assert path.stat().st_size > 0
    assert (
        LocalFileStore(path=path).get_precedent_mapping("LSEG", "LSEG-CARBON-029")
        is not None
    )


def test_an_unreadable_tail_separates_rather_than_failing_the_decision(
    tmp_path, monkeypatch
):
    """If the torn-line guard cannot read the last byte it is blind.

    Answering "looks fine" would let the next record fuse onto a half-written
    one -- the thing the guard exists to prevent. But FAILING is not the only
    alternative, and it is the wrong one: a journal that is writable but not
    readable (0o200, or a restrictive NFS/VDI ACL) would reject every review
    decision even though the append itself works fine. Writing the separator
    unconditionally is safe both ways, so the decision must still succeed."""
    from pathlib import Path as _Path

    path = tmp_path / "precedents.jsonl"
    _approve(LocalFileStore(path=path))
    store = LocalFileStore(path=path)

    # Fail the TAIL READ specifically -- the "rb" open inside
    # _needs_separator -- while leaving the append itself working. This is
    # the real code path; patching the method away would pass against any
    # implementation and prove nothing.
    real_open = _Path.open

    def open_fails_for_binary_reads(self, mode="r", *args, **kwargs):
        if "b" in mode and "r" in mode:
            raise OSError("tail unreadable")
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(_Path, "open", open_fails_for_binary_reads)
    later = VendorProductRef(vendor="LSEG", product_id="LSEG-LATER-002", name="Later")
    _approve(store, ref=later)
    monkeypatch.undo()

    # The decision stands, in memory and on disk after a restart.
    assert store.get_precedent_mapping("LSEG", "LSEG-LATER-002") is not None
    assert (
        LocalFileStore(path=path).get_precedent_mapping("LSEG", "LSEG-LATER-002")
        is not None
    )


def test_a_write_only_journal_still_accepts_decisions(tmp_path):
    """The concrete case the rule above exists for.

    A journal the process can append to but not read back -- mode 0o200, or
    the equivalent ACL on a corporate network mount -- must not turn every
    HITL decision into an error. Durability is unaffected: the append and its
    fsync both succeed; only the torn-tail probe is blind."""
    path = tmp_path / "precedents.jsonl"
    store = LocalFileStore(path=path)
    _approve(store)

    path.chmod(0o200)
    try:
        later = VendorProductRef(
            vendor="LSEG", product_id="LSEG-WRITEONLY-001", name="Later"
        )
        _approve(store, ref=later)
    finally:
        path.chmod(0o600)

    assert store.get_precedent_mapping("LSEG", "LSEG-WRITEONLY-001") is not None
    assert (
        LocalFileStore(path=path).get_precedent_mapping("LSEG", "LSEG-WRITEONLY-001")
        is not None
    )


def test_a_tail_seek_that_fails_is_not_read_as_untorn(tmp_path, monkeypatch):
    """The sibling of the test above, one layer deeper -- and the one that
    actually exercises the guard.

    The test above fails the OPEN, so the tail check never gets as far as
    seeking. This one lets the open succeed and fails only the seek, which is
    where a real I/O error on a network mount would land. Answering "not torn"
    while blind is what fuses the next record onto a half-written one: the
    crash then costs the NEXT decision as well as its own, and neither can be
    recovered because the fused line no longer parses. The decision itself
    must still succeed -- being unable to probe the tail is not a durability
    failure -- so what is asserted here is the absence of fusion, not an
    error."""
    import errno as _errno
    from pathlib import Path as _Path

    path = tmp_path / "precedents.jsonl"
    _approve(LocalFileStore(path=path))
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"ref": {"vendor": "LSEG", "produ')  # crash: no newline

    store = LocalFileStore(path=path)

    class _SeekFails:
        """A real file handle whose lseek -- and only lseek -- fails."""

        def __init__(self, fh):
            self._fh = fh

        def seek(self, *args, **kwargs):
            raise OSError(_errno.EIO, "simulated I/O error from lseek")

        def __getattr__(self, name):
            return getattr(self._fh, name)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return self._fh.__exit__(*exc)

    real_open = _Path.open

    def open_wraps_binary_reads(self, mode="r", *args, **kwargs):
        fh = real_open(self, mode, *args, **kwargs)
        if "b" in mode and "r" in mode:
            return _SeekFails(fh)
        return fh

    monkeypatch.setattr(_Path, "open", open_wraps_binary_reads)
    later = VendorProductRef(vendor="LSEG", product_id="LSEG-LATER-003", name="Later")
    _approve(store, ref=later)
    monkeypatch.undo()

    assert store.get_precedent_mapping("LSEG", "LSEG-LATER-003") is not None
    # The point of the test: nothing was fused onto the torn tail, so the only
    # unparseable line is the pre-seeded one, not a new record welded onto it
    # -- and the new decision survives a restart as its own parseable record.
    assert (
        LocalFileStore(path=path).get_precedent_mapping("LSEG", "LSEG-LATER-003")
        is not None
    )
    unparseable = 0
    for ln in path.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        try:
            json.loads(ln)
        except ValueError:
            unparseable += 1
    assert unparseable == 1


def test_a_rollback_never_deletes_another_writers_committed_record(tmp_path):
    """The rollback must remove its OWN failed append and nothing else.

    self._lock is per-INSTANCE and there is no file lock, so a second store on
    the same journal -- another instance here, another OS process in the real
    deployment -- can commit between the offset capture and the rollback. A
    blind truncate back to that stale offset would silently destroy a decision
    whose caller was told it SUCCEEDED and whose memory says approved: an
    unrecoverable, undetectable loss, strictly worse than the visible phantom
    record the rollback exists to clean up."""
    import errno as _errno

    path = tmp_path / "precedents.jsonl"
    writer_a = LocalFileStore(path=path)
    writer_b = LocalFileStore(path=path)
    _approve(writer_a)

    other = VendorProductRef(
        vendor="LSEG", product_id="LSEG-OTHER-WRITER", name="Other"
    )

    def commits_then_fails(created):
        # B lands a real, committed decision while A is mid-write; A's
        # directory fsync then fails for real -- the case the rollback is for.
        _approve(writer_b, ref=other)
        raise OSError(_errno.EIO, "simulated real I/O error on directory fsync")

    writer_a._sync_parent_directory = commits_then_fails
    later = VendorProductRef(vendor="LSEG", product_id="LSEG-ROLLED-BACK", name="Later")
    with pytest.raises(OSError):
        _approve(writer_a, ref=later)

    # B's decision survives, on disk and across a restart. A's did not commit.
    restarted = LocalFileStore(path=path)
    assert restarted.get_precedent_mapping("LSEG", "LSEG-OTHER-WRITER") is not None
    assert writer_a.get_precedent_mapping("LSEG", "LSEG-ROLLED-BACK") is None


def test_a_rollback_after_writing_nothing_leaves_the_journal_alone(
    tmp_path, monkeypatch
):
    """The variant with no append at all.

    If the failure arrives before any bytes are written, there is nothing of
    ours in the file -- so truncating to the captured offset would delete
    purely other people's records. Nothing must be removed."""
    import errno as _errno
    from pathlib import Path as _Path

    path = tmp_path / "precedents.jsonl"
    writer_a = LocalFileStore(path=path)
    writer_b = LocalFileStore(path=path)
    _approve(writer_a)

    other = VendorProductRef(vendor="LSEG", product_id="LSEG-OTHER-NOAPPEND", name="O")
    real_open = _Path.open
    state = {"tripped": False}

    def fails_the_append_after_b_commits(self, mode="r", *args, **kwargs):
        if "a" in mode and not state["tripped"]:
            state["tripped"] = True
            _approve(writer_b, ref=other)
            raise OSError(_errno.EIO, "simulated I/O error opening for append")
        return real_open(self, mode, *args, **kwargs)

    later = VendorProductRef(vendor="LSEG", product_id="LSEG-NOT-WRITTEN", name="Later")
    monkeypatch.setattr(_Path, "open", fails_the_append_after_b_commits)
    with pytest.raises(OSError):
        _approve(writer_a, ref=later)
    monkeypatch.undo()

    restarted = LocalFileStore(path=path)
    assert restarted.get_precedent_mapping("LSEG", "LSEG-OTHER-NOAPPEND") is not None
    assert restarted.get_precedent_mapping("LSEG", "LSEG-NOT-WRITTEN") is None


def test_missing_file_is_a_clean_empty_start(tmp_path):
    store = LocalFileStore(path=tmp_path / "does_not_exist_yet.jsonl")
    assert store.get_precedent_mapping("LSEG", "LSEG-CARBON-029") is None
