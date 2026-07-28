import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omenled import protocol


class TestBuild:
    def test_pads_to_full_report(self):
        assert len(protocol.build([0x01, 0x02])) == protocol.REPORT_SIZE

    def test_preserves_leading_bytes_and_zeroes_the_rest(self):
        packet = protocol.build([0x01, 0xD0, 0xFF])
        assert packet[:3] == b"\x01\xd0\xff"
        assert set(packet[3:]) == {0}

    def test_rejects_oversized_input(self):
        with pytest.raises(ValueError):
            protocol.build([0] * (protocol.REPORT_SIZE + 1))

    def test_rejects_values_outside_a_byte(self):
        with pytest.raises(ValueError):
            protocol.build([256])


class TestBuildProbe:
    def test_matches_the_packet_shape_recorded_from_the_device(self):
        # The one TX the previous attempt logged alongside both a reply and a
        # visible LED change.
        packet = protocol.build_probe(r=0xFF, g=0x00, b=0x00)
        assert packet[:11] == bytes(
            [0x01, 0x00, 0x00, 0xD0, 0xFF, 0x00, 0x00, 0x0F, 0x06, 0x00, 0x03]
        )

    def test_is_always_a_full_length_report(self):
        assert len(protocol.build_probe(1, 2, 3)) == protocol.REPORT_SIZE

    def test_each_field_lands_where_the_hypothesis_says(self):
        packet = protocol.build_probe(opcode=0xAB, index=0xCD, brightness=0x7F)
        assert packet[0] == 0xAB
        assert packet[1] == 0xCD
        assert packet[7] == 0x7F


class TestWithByte:
    def test_changes_exactly_one_position(self):
        base = protocol.build_probe()
        changed = protocol.with_byte(base, 5, 0x42)
        assert changed[5] == 0x42
        assert protocol.diff(base, changed) == [protocol.ByteDiff(5, base[5], 0x42)]

    def test_rejects_offsets_outside_the_report(self):
        with pytest.raises(ValueError):
            protocol.with_byte(protocol.build_probe(), protocol.REPORT_SIZE, 0)


class TestDiff:
    def test_empty_when_identical(self):
        packet = protocol.build_probe(1, 2, 3)
        assert protocol.diff(packet, packet) == []

    def test_reports_every_changed_offset(self):
        left = protocol.build([0x00, 0x00, 0x00])
        right = protocol.build([0x00, 0x11, 0x22])
        assert [d.offset for d in protocol.diff(left, right)] == [1, 2]

    def test_rejects_mismatched_lengths(self):
        with pytest.raises(ValueError):
            protocol.diff(b"\x00", b"\x00\x00")

    def test_str_includes_the_hypothesis_note(self):
        assert "opcode" in str(protocol.ByteDiff(0, 0x01, 0x02))


class TestSurvey:
    def test_separates_moving_offsets_from_fixed_ones(self):
        samples = [
            protocol.build([0xAA, 0x00, 0x01]),
            protocol.build([0xAA, 0x00, 0x02]),
            protocol.build([0xAA, 0x00, 0x03]),
        ]
        result = protocol.survey(samples)
        assert list(result.varying) == [2]
        assert result.varying[2] == [1, 2, 3]
        assert result.constant[0] == 0xAA

    def test_rejects_ragged_samples(self):
        with pytest.raises(ValueError):
            protocol.survey([b"\x00", b"\x00\x00"])

    def test_rejects_empty_input(self):
        with pytest.raises(ValueError):
            protocol.survey([])

    def test_report_is_explicit_when_nothing_moved(self):
        result = protocol.survey([protocol.build([1, 2, 3])] * 3)
        assert "none" in result.report()


class TestHelpers:
    def test_annotate_labels_known_offsets(self):
        text = protocol.annotate(protocol.build_probe(r=0xFF))
        assert "opcode" in text
        assert "01:" not in text.split("\n")[0]  # first line starts at offset 00
