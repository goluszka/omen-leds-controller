"""Framing tests.

The 64-vs-65 byte distinction is the single defect that made the previous
attempt's findings unreproducible, so it gets covered directly rather than
trusted to review.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omenled import protocol, safety, transport
from omenled.journal import Journal


class FakeHidDevice:
    """Records exactly what hidapi would have been handed."""

    def __init__(self, replies=None):
        self.written = []
        self.replies = list(replies or [])
        self.closed = False

    def open_path(self, path):
        self.path = path

    def get_manufacturer_string(self):
        return "ENE"

    def get_product_string(self):
        return "OMEN 27k External Light Device"

    def write(self, buf):
        self.written.append(bytes(buf))
        return len(buf)

    def read(self, size, timeout_ms=0):
        return list(self.replies.pop(0)) if self.replies else []

    def get_feature_report(self, report_id, size):
        raise OSError("read error")

    def close(self):
        self.closed = True


@pytest.fixture
def fake_hid(monkeypatch):
    device = FakeHidDevice()

    class FakeHidModule:
        @staticmethod
        def enumerate(vid, pid):
            return [{"path": b"fake:1", "interface_number": 1}]

        @staticmethod
        def device():
            return device

    monkeypatch.setattr(transport, "hid", FakeHidModule)
    return device


@pytest.fixture
def link(fake_hid, tmp_path):
    with Journal(session="test", directory=str(tmp_path)) as journal:
        with transport.Transport(journal, min_interval=0) as link:
            yield link


class TestFraming:
    def test_a_64_byte_payload_goes_out_as_65_bytes(self, link, fake_hid):
        link.write(protocol.build_probe(r=0xFF))
        assert len(fake_hid.written[0]) == 65

    def test_the_report_id_byte_is_zero_and_added_here(self, link, fake_hid):
        link.write(protocol.build_probe(r=0xFF))
        assert fake_hid.written[0][0] == 0x00

    def test_the_device_receives_the_payload_unaltered(self, link, fake_hid):
        payload = protocol.build_probe(r=0x11, g=0x22, b=0x33)
        link.write(payload)
        assert fake_hid.written[0][1:] == payload

    def test_short_payloads_are_refused(self, link):
        with pytest.raises(transport.TransportError, match="exactly 64 bytes"):
            link.write(bytes(63))

    def test_long_payloads_are_refused(self, link):
        with pytest.raises(transport.TransportError, match="exactly 64 bytes"):
            link.write(bytes(65))

    def test_a_short_write_by_the_driver_raises(self, link, fake_hid):
        fake_hid.write = lambda buf: 64  # driver truncated the report
        with pytest.raises(transport.TransportError, match="did not receive a full"):
            link.write(protocol.build_probe())


class TestPad:
    def test_builds_a_full_report_from_leading_bytes(self):
        assert len(transport.pad(0x01, 0x00, 0xD0)) == 64

    def test_refuses_more_bytes_than_fit(self):
        with pytest.raises(ValueError):
            transport.pad(*([0] * 65))


class TestReadAndExchange:
    def test_timeout_is_reported_as_none(self, link):
        assert link.read(timeout_ms=1) is None

    def test_a_reply_comes_back_as_bytes(self, link, fake_hid):
        fake_hid.replies.append(bytes(range(64)))
        assert link.read() == bytes(range(64))

    def test_exchange_writes_then_reads(self, link, fake_hid):
        fake_hid.replies.append(bytes(64))
        reply = link.exchange(protocol.build_probe())
        assert len(fake_hid.written) == 1
        assert reply == bytes(64)


class TestDryRun:
    def test_nothing_reaches_the_device(self, link, fake_hid):
        link.write(protocol.build_probe(), dry_run=True)
        assert fake_hid.written == []

    def test_the_intended_packet_is_still_journalled(self, link, tmp_path):
        link.write(protocol.build_probe(r=0xAB), dry_run=True)
        text = open(link.journal.path).read()
        assert "ab" in text and "dry_run" in text


class TestSafetyIntegration:
    def test_a_dangerous_opcode_is_refused_by_default(self, link):
        with pytest.raises(safety.UnsafePacket):
            link.write(protocol.build([0x5A, 0xA5]))

    def test_override_sends_it_and_records_why(self, link, fake_hid):
        link.write(protocol.build([0x5A, 0xA5]), override_safety=True)
        assert len(fake_hid.written) == 1
        assert "safety override" in open(link.journal.path).read()


class TestJournalling:
    def test_every_transmit_is_logged_without_the_caller_asking(self, link):
        link.write(protocol.build_probe(r=0x77))
        assert '"kind": "tx"' in open(link.journal.path).read()

    def test_a_timeout_is_logged_rather_than_dropped(self, link):
        link.read(timeout_ms=1)
        assert '"timeout": true' in open(link.journal.path).read()


class TestLifecycle:
    def test_missing_device_names_what_to_check(self, monkeypatch, tmp_path):
        class NoDevices:
            @staticmethod
            def enumerate(vid, pid):
                return []

        monkeypatch.setattr(transport, "hid", NoDevices)
        with Journal(session="t", directory=str(tmp_path)) as journal:
            with pytest.raises(transport.DeviceNotFound, match="USB upstream cable"):
                transport.Transport(journal).open()

    def test_closing_runs_the_release_hook(self, fake_hid, tmp_path):
        called = []
        with Journal(session="t", directory=str(tmp_path)) as journal:
            link = transport.Transport(journal, min_interval=0).open()
            link.set_release_hook(lambda t: called.append(True))
            link.close()
        assert called == [True]

    def test_release_runs_once_only(self, fake_hid, tmp_path):
        called = []
        with Journal(session="t", directory=str(tmp_path)) as journal:
            link = transport.Transport(journal, min_interval=0).open()
            link.set_release_hook(lambda t: called.append(True))
            link.close()
            link._release_quietly()
        assert called == [True]

    def test_a_failing_release_does_not_propagate(self, fake_hid, tmp_path):
        def boom(_):
            raise RuntimeError("no release command known yet")

        with Journal(session="t", directory=str(tmp_path)) as journal:
            link = transport.Transport(journal, min_interval=0).open()
            link.set_release_hook(boom)
            link.close()  # must not raise
            assert "release failed" in open(journal.path).read()

    def test_using_a_closed_transport_is_an_error(self, fake_hid, tmp_path):
        with Journal(session="t", directory=str(tmp_path)) as journal:
            link = transport.Transport(journal, min_interval=0)
            with pytest.raises(transport.TransportError, match="not open"):
                link.read()
