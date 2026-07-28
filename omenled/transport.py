"""The single place raw bytes reach the device.

Everything that talks to the monitor goes through here, for one reason: the
previous attempt at this protocol produced unreproducible results because some
writes were 64 bytes and some were 63, which silently shifts every field offset.
This module makes a short report unrepresentable.

Framing, spelled out because it is the easiest thing to get wrong:

    The report descriptor declares a 64-byte output report and *no report IDs*.
    hidapi always wants a leading report-ID byte; when that byte is 0x00 it
    strips it and transmits the rest. So a correct 64-byte report is written as
    65 bytes (0x00 + 64 payload), and hidapi returns 65 on success.

    write(b"\\x00" + 64 bytes) -> returns 65 -> device receives 64 bytes  correct
    write(b"\\x00" + 63 bytes) -> returns 64 -> device receives 63 bytes  malformed
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

import hid

from . import safety
from .journal import Journal

VID = 0x03F0
PID = 0x07B0
REPORT_SIZE = 64
FEATURE_SIZE = 6


class DeviceNotFound(Exception):
    pass


class TransportError(Exception):
    pass


def find_devices(vid: int = VID, pid: int = PID) -> List[Dict[str, Any]]:
    return list(hid.enumerate(vid, pid))


def describe() -> List[Dict[str, Any]]:
    """Enumeration detail, safe to call with no device open."""
    return [
        {
            "path": d["path"].decode(errors="replace") if isinstance(d["path"], bytes) else d["path"],
            "interface_number": d.get("interface_number"),
            "usage_page": hex(d.get("usage_page", 0)),
            "usage": hex(d.get("usage", 0)),
            "product_string": d.get("product_string"),
            "serial_number": d.get("serial_number"),
            "release_number": d.get("release_number"),
        }
        for d in find_devices()
    ]


class Transport:
    """Open HID connection to the lighting controller.

    Use as a context manager. Every transmit and receive is journalled without
    the caller having to remember, so no experiment can produce an unlogged
    result.
    """

    def __init__(
        self,
        journal: Journal,
        vid: int = VID,
        pid: int = PID,
        min_interval: float = 0.05,
    ):
        self.journal = journal
        self.vid = vid
        self.pid = pid
        self._limiter = safety.RateLimiter(min_interval)
        self._dev: Optional[Any] = None
        self._release_hook: Optional[Callable[["Transport"], None]] = None
        self._released = False

    # -- lifecycle ---------------------------------------------------------

    def open(self) -> "Transport":
        candidates = find_devices(self.vid, self.pid)
        if not candidates:
            raise DeviceNotFound(
                f"no HID device {self.vid:04x}:{self.pid:04x} "
                "(OMEN 27k External Light Device). Check the monitor's USB "
                "upstream cable is connected to this Mac."
            )
        dev = hid.device()
        dev.open_path(candidates[0]["path"])
        self._dev = dev
        self._released = False
        self.journal.note(
            f"opened {self.vid:04x}:{self.pid:04x} "
            f"({dev.get_manufacturer_string()} / {dev.get_product_string()})"
        )
        safety.on_exit(self._release_quietly)
        return self

    def close(self) -> None:
        if self._dev is not None:
            self._release_quietly()
            self._dev.close()
            self._dev = None
            self.journal.note("closed")

    def __enter__(self) -> "Transport":
        return self.open()

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @property
    def device(self) -> Any:
        if self._dev is None:
            raise TransportError("transport is not open")
        return self._dev

    # -- control handover --------------------------------------------------

    def set_release_hook(self, fn: Callable[["Transport"], None]) -> None:
        """Install the routine that hands lighting control back to the OSD.

        Unknown at the time of writing — finding it is step 1 of the decode.
        Until then this stays unset and the monitor may need a USB replug.
        """
        self._release_hook = fn

    def _release_quietly(self) -> None:
        if self._released or self._dev is None or self._release_hook is None:
            return
        self._released = True
        try:
            self._release_hook(self)
            self.journal.note("released lighting control back to OSD")
        except Exception as exc:
            self.journal.note(f"release failed: {exc!r}")

    # -- wire --------------------------------------------------------------

    def write(
        self,
        payload: bytes,
        label: Optional[str] = None,
        override_safety: bool = False,
        dry_run: bool = False,
    ) -> int:
        """Send one 64-byte output report.

        `payload` is exactly the 64 bytes the device should see. The report-ID
        byte is added here and nowhere else.
        """
        if len(payload) != REPORT_SIZE:
            raise TransportError(
                f"payload must be exactly {REPORT_SIZE} bytes, got {len(payload)}. "
                "Pad it — a short report silently shifts every field offset."
            )

        reason = safety.check(payload, override=override_safety)
        if reason:
            self.journal.note(f"safety override: {reason}", label=label)

        if dry_run:
            self.journal.record("tx", label=label, hex=" ".join(f"{b:02x}" for b in payload),
                                dry_run=True)
            return REPORT_SIZE + 1

        self._limiter.wait()
        self.journal.tx(payload, label=label)
        written = self.device.write(b"\x00" + payload)
        if written != REPORT_SIZE + 1:
            raise TransportError(
                f"hid_write returned {written}, expected {REPORT_SIZE + 1}. "
                "The device did not receive a full 64-byte report."
            )
        return written

    def read(self, timeout_ms: int = 500, label: Optional[str] = None) -> Optional[bytes]:
        """Read one input report, or None on timeout.

        None is meaningful: whether a command answers at all is evidence about
        the protocol, so it is journalled rather than swallowed.
        """
        raw = self.device.read(REPORT_SIZE, timeout_ms)
        payload = bytes(raw) if raw else None
        self.journal.rx(payload, label=label)
        return payload

    def exchange(
        self,
        payload: bytes,
        timeout_ms: int = 500,
        label: Optional[str] = None,
        override_safety: bool = False,
        dry_run: bool = False,
    ) -> Optional[bytes]:
        """Write then read — the shape almost every probe wants."""
        self.write(payload, label=label, override_safety=override_safety, dry_run=dry_run)
        if dry_run:
            return None
        return self.read(timeout_ms=timeout_ms, label=label)

    # -- feature reports ---------------------------------------------------

    def get_feature(self, report_id: int = 0, label: Optional[str] = None) -> Optional[bytes]:
        """Read the 6-byte feature report, if the device permits it."""
        try:
            raw = self.device.get_feature_report(report_id, FEATURE_SIZE + 1)
        except (OSError, ValueError) as exc:
            self.journal.record("note", label=label, note=f"get_feature({report_id}) failed: {exc}")
            return None
        payload = bytes(raw) if raw else None
        self.journal.record(
            "rx", label=label, feature=True,
            hex=" ".join(f"{b:02x}" for b in payload) if payload else None,
        )
        return payload


def pad(*values: int) -> bytes:
    """Build a full-length report from its leading bytes.

    Probes are written as `pad(0x01, 0x00, 0x00, 0xd0, r, g, b)` so the padding
    is never miscounted by hand — which is precisely how the previous attempt
    ended up sending 63-byte reports.
    """
    if len(values) > REPORT_SIZE:
        raise ValueError(f"{len(values)} bytes exceeds the {REPORT_SIZE}-byte report")
    return bytes(values) + bytes(REPORT_SIZE - len(values))
