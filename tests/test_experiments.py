"""How the workbench reads a human's answer.

This is where a decode most easily fools itself, and did: three separate
false-positive verdicts came from ad-hoc string comparison. The real answers
that caused them are used as test cases.
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import probe
from omenled import protocol


class TestClassify:
    @pytest.mark.parametrize(
        "answer,expected",
        [
            ("red", "red"), ("went red", "red"), ("czerwony", "red"),
            ("all green now", "green"), ("zielony", "green"),
            ("blue", "blue"), ("white", "white"), ("biały", "white"),
            ("leds went out", "off"), ("zgasły", "off"),
            ("nothing", "nothing"), ("nic", "nothing"),
        ],
    )
    def test_recognises_answers_in_either_language(self, answer, expected):
        assert expected in probe.classify(answer)

    def test_an_unrecognised_answer_yields_nothing_rather_than_a_guess(self):
        assert probe.classify("hmm, hard to say") == []

    @pytest.mark.parametrize(
        "answer,not_expected",
        [
            ("flickered oddly", "red"),      # 'flicke-RED-'
            ("it looks nice", "nothing"),    # 'NIC-e'
            ("output was odd", "off"),       # 'OUT-put'
            ("scattered light", "red"),      # 'scatte-RED-'
        ],
    )
    def test_words_that_merely_contain_a_keyword_are_not_matched(self, answer, not_expected):
        # A substring match manufactures evidence from an ambiguous answer,
        # which is the one failure a decode cannot recover from.
        assert not_expected not in probe.classify(answer)


class TestPosition:
    @pytest.mark.parametrize(
        "answer",
        ["left bottom", "left bottom, slighlty shifted", "bottom left",
         "red left bottom segment, slightly smaller", "lewy dolny"],
    )
    def test_the_same_place_described_differently_normalises_alike(self, answer):
        assert probe.position(answer) == ("bottom", "left")

    def test_a_qualifier_does_not_outvote_the_subject(self):
        answer = "red bottom left segment, with slight part of right bottom segment"
        assert probe.position(answer) == ("bottom", "left")

    def test_genuinely_different_places_differ(self):
        assert probe.position("top right") != probe.position("bottom left")

    def test_an_answer_with_no_position_words_is_unrecognised(self):
        assert probe.position("it went red") is None


class TestReportsChange:
    REFERENCE = "small part of bottom left segment"

    @pytest.mark.parametrize(
        "answer",
        ["the same", "same", "The same", "no change", "identical",
         "unchanged", "bez zmian", "to samo", "", "   "],
    )
    def test_answers_meaning_nothing_changed(self, answer):
        assert not probe.reports_change(answer, self.REFERENCE)

    @pytest.mark.parametrize(
        "answer",
        ["top right now", "moved to the right", "shifted by one",
         "przesunięty w prawo", "bottom right segment"],
    )
    def test_answers_describing_a_change(self, answer):
        assert probe.reports_change(answer, self.REFERENCE)

    def test_the_same_place_worded_differently_is_not_a_change(self):
        assert not probe.reports_change("bottom left, small bit", self.REFERENCE)

    def test_an_ambiguous_answer_counts_as_no_change(self):
        assert not probe.reports_change("hard to say really", self.REFERENCE)

    @pytest.mark.parametrize("answer", ["brighter", "dimmer", "darker", "jaśniej"])
    def test_comparatives_register_as_change(self, answer):
        assert probe.reports_change(answer, "normal")

    def test_a_bare_dark_is_not_a_comparative(self):
        assert not probe.reports_change("still dark", "dark")

    def test_a_different_ordinal_is_a_change(self):
        assert probe.reports_change("second led", "first led")

    def test_a_different_digit_is_a_change(self):
        assert probe.reports_change("LED 9", "LED 1")

    def test_numbers_only_on_one_side_are_not_evidence(self):
        assert not probe.reports_change("LED 4", "somewhere in the corner")

    def test_a_same_marker_wins_over_numbers(self):
        assert not probe.reports_change("the same, led 9", "led 1")


class TestLitReport:
    def test_it_is_a_full_length_report(self):
        assert len(probe.lit_report(protocol.PLANE_RED)) == protocol.REPORT_SIZE

    def test_the_opcode_is_selectable(self):
        assert probe.lit_report(0x11)[0] == 0x11

    def test_it_carries_the_header_the_device_accepts(self):
        assert probe.lit_report(0x05)[:4] == bytes([0x05, 0x00, 0x00, 0xD0])

    def test_every_led_byte_is_lit(self):
        report = probe.lit_report(protocol.PLANE_RED)
        assert set(report[protocol.DATA_FIRST:]) == {0xFF}

    def test_the_level_is_selectable(self):
        report = probe.lit_report(protocol.PLANE_RED, value=0x80)
        assert set(report[protocol.DATA_FIRST:]) == {0x80}


class TestVerdictOpcodes:
    def test_a_new_opcode_is_called_out(self):
        text = probe.verdict_opcodes({"05": "red", "11": "went green"})
        assert "NEW: 11" in text
        assert "docs/protocol.md" in text

    def test_the_known_four_are_marked_and_not_reported_as_new(self):
        text = probe.verdict_opcodes({"05": "red", "06": "green"})
        assert "(known)" in text
        assert "NEW" not in text

    def test_nothing_responding_is_stated_plainly(self):
        text = probe.verdict_opcodes({"01": "nothing", "02": "no visible change"})
        assert "none" in text

    def test_blank_answers_are_not_counted(self):
        assert "none" in probe.verdict_opcodes({"01": "", "02": ""})


class TestVerdictHwBright:
    def test_a_changing_byte_is_named_as_hardware_brightness(self):
        text = probe.verdict_hwbright(
            {"reference": "normal", "byte1 = ff": "brighter", "byte2 = ff": "the same"}
        )
        assert "byte1 = ff" in text
        assert "hardware brightness control" in text

    def test_no_change_points_at_the_software_path(self):
        text = probe.verdict_hwbright(
            {"reference": "normal", "byte1 = ff": "the same", "byte2 = ff": "same"}
        )
        assert "No header byte changed the brightness" in text
        assert "brightness 40" in text

    def test_it_states_the_cost_of_software_scaling(self):
        assert "quantise" in probe.verdict_hwbright({"reference": "x", "byte1": "same"})


class TestReleaseCandidates:
    def test_it_skips_the_opcodes_whose_meaning_is_known(self):
        names = [name for name, _ in probe.release_candidates()]
        for known in ("opcode 05", "opcode 06", "opcode 07", "opcode 0d"):
            assert known not in names

    def test_it_skips_the_refusal_list(self):
        names = [name for name, _ in probe.release_candidates()]
        assert "opcode 1a" in names or names  # sanity: the list is non-empty
        assert "opcode ff" not in names

    def test_every_candidate_is_a_full_report(self):
        assert all(len(p) == protocol.REPORT_SIZE for _, p in probe.release_candidates())


class TestParsing:
    def test_range_syntax_is_inclusive(self):
        assert probe.parse_range("0x00:0x03") == [0, 1, 2, 3]

    def test_comma_syntax(self):
        assert probe.parse_range("0,1,5") == [0, 1, 5]

    def test_a_byte_must_fit_in_a_byte(self):
        with pytest.raises(Exception):
            probe.parse_byte("256")


class TestExperimentRegistry:
    def test_only_the_open_questions_remain(self):
        assert set(probe.EXPERIMENTS) == {"hwbright", "opcodes"}

    def test_every_experiment_has_a_runner_verdict_and_blurb(self):
        for run, verdict, blurb in probe.EXPERIMENTS.values():
            assert callable(run) and callable(verdict) and blurb.strip()

    def test_unknown_names_list_what_is_available(self, capsys):
        class Args:
            name = "nope"
            timeout = 500

        assert probe.cmd_experiment(Args()) == 2
        assert "hwbright" in capsys.readouterr().out
