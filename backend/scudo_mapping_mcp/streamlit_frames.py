"""Session-isolated authoritative frames for the Streamlit surface."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable, MutableMapping
from typing import Optional

from .models import VendorProductRef

SessionFrames = MutableMapping[tuple[str, str], VendorProductRef]
FrameReader = Callable[[str, str], Optional[VendorProductRef]]


class StreamlitFrameRegistry:
    """Process-local frame sets partitioned by an unguessable session key."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, dict[tuple[str, str], VendorProductRef]] = {}

    def add(self, session_key: str, refs: Iterable[VendorProductRef]) -> None:
        with self._lock:
            session = self._sessions.setdefault(session_key, {})
            add_session_frames(session, refs)

    def read(
        self, session_key: str, vendor: str, product_id: str
    ) -> Optional[VendorProductRef]:
        with self._lock:
            return self._sessions.get(session_key, {}).get((vendor, product_id))

    def clear(self, session_key: str) -> None:
        with self._lock:
            self._sessions.pop(session_key, None)


def add_session_frames(
    session: SessionFrames, refs: Iterable[VendorProductRef]
) -> None:
    for ref in refs:
        session[(ref.vendor, ref.product_id)] = ref


def read_session_frame(
    session: SessionFrames,
    vendor: str,
    product_id: str,
    *,
    fallback: FrameReader,
    frame_source: str,
) -> Optional[VendorProductRef]:
    """Read a session frame locally, preserving the S3 production cutover."""
    if frame_source == "s3":
        return fallback(vendor, product_id)
    return session.get((vendor, product_id))
