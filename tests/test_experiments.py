"""Tests for the workbench's pure logic.

The interpretation of a human answer is where a decode most easily fools
itself, so the classifier and the verdicts are tested rather than eyeballed.
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import probe


class TestClassify:
    @pytest.mark.parametrize(
        "answer,expected",
        [
            ("red", "red"),
            ("went red", "red"),
            ("czerwony", "red"),
            ("all green now", "green"),
            ("zielony", "green"),
            ("blue", "blue"),
            ("white", "white"),
            ("biały", "white"),
            ("leds went out", "off"),
            ("zgasły", "off"),
            ("nothing", "nothing"),
            ("nic", "nothing"),
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
        # A substring match here would manufacture evidence from an ambiguous
        # answer, which is the one failure mode a decode cannot recover from.
        assert not_expected not in probe.classify(answer)

    def test_the_default_no_change_string_reads_as_nothing(self):
        assert "nothing" in probe.classify("no visible change")


class TestVerdictCommit:
    def test_colour_in_the_0d_packet_means_a_direct_write(self):
        text = probe.verdict_commit(
            {"0d+red": "red", "0d+green": "green", "stage+commit": "off"}
        )
        assert "direct write" in text

    def test_staged_colour_surviving_a_black_commit_means_stage_then_commit(self):
        text = probe.verdict_commit(
            {"0d+red": "nothing", "0d+green": "nothing", "stage+commit": "blue"}
        )
        assert "Stage-then-commit confirmed" in text

    def test_staging_wins_even_when_0d_also_carried_colour(self):
        # Blue surviving a black commit can only be explained by staging.
        text = probe.verdict_commit(
            {"0d+red": "red", "0d+green": "green", "stage+commit": "blue"}
        )
        assert "Stage-then-commit confirmed" in text

    def test_blanking_regardless_of_colour_means_an_off_command(self):
        text = probe.verdict_commit(
            {"0d+red": "leds went out", "0d+green": "leds went out",
             "stage+commit": "off"}
        )
        assert "off command" in text

    def test_an_unclear_result_is_reported_as_unclear_not_forced(self):
        text = probe.verdict_commit(
            {"0d+red": "flickered oddly", "0d+green": "", "stage+commit": ""}
        )
        assert "no model is established" in text

    def test_missing_answers_do_not_raise(self):
        assert probe.verdict_commit({})


class TestVerdictOpcodes:
    def test_lists_only_the_opcodes_that_did_something(self):
        text = probe.verdict_opcodes(
            {"01": "nothing", "05": "went white", "0d": "leds went out"}
        )
        assert "05" in text and "0d" in text
        assert "\n  01" not in text

    def test_says_so_plainly_when_nothing_responded(self):
        text = probe.verdict_opcodes({"01": "nothing", "02": "no visible change"})
        assert "No opcode" in text

    def test_blank_answers_are_not_counted_as_responses(self):
        assert "No opcode" in probe.verdict_opcodes({"01": "", "02": ""})


class TestParsing:
    def test_range_syntax_is_inclusive(self):
        assert probe.parse_range("0x00:0x03") == [0, 1, 2, 3]

    def test_comma_syntax(self):
        assert probe.parse_range("0,1,5") == [0, 1, 5]

    def test_hex_and_decimal_both_work(self):
        assert probe.parse_range("0x10,16") == [16, 16]

    def test_a_byte_must_fit_in_a_byte(self):
        with pytest.raises(Exception):
            probe.parse_byte("256")


class TestExperimentRegistry:
    def test_every_experiment_has_a_runner_verdict_and_blurb(self):
        for name, entry in probe.EXPERIMENTS.items():
            run, verdict, blurb = entry
            assert callable(run) and callable(verdict)
            assert blurb.strip()

    def test_unknown_names_list_what_is_available(self, capsys):
        class Args:
            name = "nope"
            timeout = 500

        assert probe.cmd_experiment(Args()) == 2
        assert "commit" in capsys.readouterr().out
