import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omenled import protocol, safety


class TestExplain:
    def test_an_ordinary_packet_passes(self):
        assert safety.explain(protocol.build_probe(r=0xFF)) is None

    @pytest.mark.parametrize("magic", [(0x5A, 0xA5), (0xA5, 0x5A), (0x55, 0xAA), (0xAA, 0x55)])
    def test_flash_unlock_magics_are_named(self, magic):
        reason = safety.explain(protocol.build(list(magic)))
        assert "flash-unlock magic" in reason

    @pytest.mark.parametrize("opcode", [0xF0, 0xFF, 0xFE])
    def test_bootloader_selectors_are_named(self, opcode):
        assert "bootloader" in safety.explain(protocol.build([opcode, 0x00]))

    @pytest.mark.parametrize("opcode", [0x01, 0x02, 0x05, 0x06, 0x0D, 0x20, 0x80])
    def test_the_device_s_own_command_range_is_not_refused(self, opcode):
        # These collide with raw SPI-NOR opcodes, but 0x01/0x05/0x0D are the
        # commands observed driving this controller. Screening them would make
        # the override reflexive.
        assert safety.explain(protocol.build([opcode, 0x00])) is None

    def test_a_stub_payload_is_refused_rather_than_indexed_past_its_end(self):
        assert safety.explain(b"\x01") is not None


class TestCheck:
    def test_passes_quietly(self):
        assert safety.check(protocol.build_probe()) is None

    def test_raises_on_a_dangerous_payload(self):
        with pytest.raises(safety.UnsafePacket, match="refusing to send"):
            safety.check(protocol.build([0x5A, 0xA5]))

    def test_the_error_says_how_to_override(self):
        with pytest.raises(safety.UnsafePacket, match="override"):
            safety.check(protocol.build([0xFF, 0x00]))

    def test_override_returns_the_reason_for_journalling(self):
        reason = safety.check(protocol.build([0x5A, 0xA5]), override=True)
        assert "flash-unlock magic" in reason


class TestIterSafe:
    def test_strips_the_refused_opcodes_from_a_sweep(self):
        swept = safety.iter_safe(range(0x00, 0x100))
        assert 0xFF not in swept  # bootloader selector
        assert 0x5A not in swept  # unlock magic
        assert 0x01 in swept      # the device's known-working command survives

    def test_leaves_an_all_safe_range_intact(self):
        assert safety.iter_safe([0x10, 0x11, 0x12]) == [0x10, 0x11, 0x12]


class TestRateLimiter:
    def test_spaces_successive_calls(self):
        import time

        limiter = safety.RateLimiter(min_interval=0.05)
        limiter.wait()
        start = time.monotonic()
        limiter.wait()
        assert time.monotonic() - start >= 0.04

    def test_a_zero_interval_does_not_block(self):
        import time

        limiter = safety.RateLimiter(min_interval=0)
        start = time.monotonic()
        for _ in range(10):
            limiter.wait()
        assert time.monotonic() - start < 0.05


class TestOnExit:
    def test_registered_cleanups_run(self):
        called = []
        safety.on_exit(lambda: called.append(1))
        safety._run_cleanups()
        assert called == [1]

    def test_a_failing_cleanup_does_not_block_the_others(self):
        called = []

        def boom():
            raise RuntimeError("device already gone")

        safety.on_exit(lambda: called.append("second"))
        safety.on_exit(boom)
        safety._run_cleanups()
        assert called == ["second"]
