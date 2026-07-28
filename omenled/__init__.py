"""Software control of the HP OMEN 27k external lighting on macOS.

Phase 1: decode the vendor HID protocol. See docs/protocol.md for the evidence
ledger and docs/superpowers/specs/ for the design.
"""

__all__ = ["journal", "protocol", "safety", "transport"]
