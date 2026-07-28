"""Brightness: pure scaling, persistence, and the CLI wiring.

The persistence rule under test is that the frame is stored unscaled. Storing
the scaled result would make repeated dimming compound, decaying colours toward
black with no way back to what the user originally asked for.
"""

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from omenled import cli, device, protocol, state, transport
from omenled.journal import Journal

from test_transport import FakeHidDevice, fake_hid  # noqa: F401


class TestScale:
    def test_full_brightness_is_the_identity(self):
        assert protocol.scale([(255, 128, 0)], 100) == [(255, 128, 0)]

    def test_zero_is_black(self):
        assert protocol.scale([(255, 255, 255)], 0) == [(0, 0, 0)]

    def test_half_halves_every_channel(self):
        assert protocol.scale([(200, 100, 50)], 50) == [(100, 50, 25)]

    def test_hue_is_preserved_as_it_dims(self):
        r, g, b = protocol.scale([(255, 128, 0)], 40)[0]
        assert (r, g, b) == (102, 51, 0)

    def test_it_scales_every_pixel(self):
        assert len(protocol.scale([(255, 0, 0)] * 60, 50)) == 60

    @pytest.mark.parametrize("bad", [-1, 101, 1000])
    def test_out_of_range_is_refused(self, bad):
        with pytest.raises(ValueError, match="0..100"):
            protocol.scale([(0, 0, 0)], bad)


class TestState:
    def test_round_trip(self, tmp_path):
        path = str(tmp_path / "s.json")
        state.save([(1, 2, 3), (4, 5, 6)], 70, path)
        assert state.load(path) == ([(1, 2, 3), (4, 5, 6)], 70)

    def test_missing_file_is_not_an_error(self, tmp_path):
        assert state.load(str(tmp_path / "absent.json")) is None

    def test_corrupt_file_is_not_an_error(self, tmp_path):
        path = tmp_path / "s.json"
        path.write_text("{not json")
        assert state.load(str(path)) is None

    def test_malformed_pixels_are_rejected(self, tmp_path):
        path = tmp_path / "s.json"
        path.write_text(json.dumps({"pixels": [[1, 2]], "brightness": 50}))
        assert state.load(str(path)) is None

    def test_missing_keys_are_rejected(self, tmp_path):
        path = tmp_path / "s.json"
        path.write_text(json.dumps({"pixels": [[1, 2, 3]]}))
        assert state.load(str(path)) is None

    def test_it_creates_the_directory(self, tmp_path):
        path = str(tmp_path / "nested" / "deep" / "s.json")
        state.save([(1, 1, 1)], 10, path)
        assert state.load(path) is not None


@pytest.fixture
def lights(fake_hid, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(state, "DEFAULT_PATH", str(tmp_path / "state.json"))
    journal = Journal(session="t", directory=str(tmp_path))
    link = transport.Transport(journal, min_interval=0).open()
    yield device.Lighting(link), fake_hid
    link.close()
    journal.close()


def run(light, argv):
    args = cli.build_parser().parse_args(argv)
    return args.func(light, args)


class TestCliBrightness:
    def test_fill_at_full_brightness_sends_full_intensities(self, lights):
        light, hid = lights
        run(light, ["fill", "red"])
        assert hid.written[0][1 + protocol.DATA_FIRST] == 255

    def test_the_brightness_flag_scales_what_is_sent(self, lights):
        light, hid = lights
        run(light, ["--brightness", "40", "fill", "red"])
        assert hid.written[0][1 + protocol.DATA_FIRST] == 102

    def test_brightness_persists_to_the_next_command(self, lights):
        light, hid = lights
        run(light, ["--brightness", "50", "fill", "red"])
        hid.written.clear()
        run(light, ["fill", "red"])
        assert hid.written[0][1 + protocol.DATA_FIRST] == 128

    def test_repeated_dimming_does_not_compound(self, lights):
        light, hid = lights
        run(light, ["fill", "red"])
        run(light, ["brightness", "50"])
        hid.written.clear()
        run(light, ["brightness", "50"])
        # Still half of full, not a quarter.
        assert hid.written[0][1 + protocol.DATA_FIRST] == 128

    def test_brightness_can_be_restored_to_full(self, lights):
        light, hid = lights
        run(light, ["--brightness", "10", "fill", "red"])
        run(light, ["brightness", "100"])
        assert hid.written[-3][1 + protocol.DATA_FIRST] == 255

    def test_brightness_before_anything_rendered_explains_itself(self, lights, capsys):
        light, _ = lights
        assert run(light, ["brightness", "50"]) == 1
        assert "nothing rendered yet" in capsys.readouterr().err

    def test_keep_restores_the_remembered_frame(self, lights):
        light, hid = lights
        run(light, ["fill", "blue"])
        hid.written.clear()
        run(light, ["set", "0", "red", "--keep"])
        blue_plane = hid.written[2][1:]
        # LED 1 keeps its blue; LED 0 became red.
        assert blue_plane[protocol.DATA_FIRST + 1] == 255
        assert blue_plane[protocol.DATA_FIRST] == 0

    def test_off_leaves_nothing_lit(self, lights):
        light, hid = lights
        run(light, ["fill", "white"])
        hid.written.clear()
        run(light, ["off"])
        assert hid.written[0][1] == protocol.OP_OFF

    def test_dry_run_does_not_write_state(self, lights, tmp_path):
        light, _ = lights
        run(light, ["--dry-run", "fill", "red"])
        assert state.load() is None


class TestPercentParsing:
    @pytest.mark.parametrize("text", ["0", "50", "100"])
    def test_accepted(self, text):
        assert cli.percent(text) == int(text)

    @pytest.mark.parametrize("text", ["-1", "101", "abc", "50.5", ""])
    def test_rejected_with_a_usable_message(self, text):
        with pytest.raises(Exception) as exc:
            cli.percent(text)
        assert "0-100" in str(exc.value)


import probe  # noqa: E402


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
        text = probe.verdict_hwbright({"reference": "x", "byte1 = ff": "same"})
        assert "quantise" in text

    @pytest.mark.parametrize("answer", ["brighter", "dimmer", "darker", "jaśniej"])
    def test_comparatives_register_as_change(self, answer):
        assert probe.reports_change(answer, "normal")

    def test_a_bare_dark_is_not_a_comparative(self):
        # "still dark" describes no change; only "darker" is evidence.
        assert not probe.reports_change("still dark", "dark")
