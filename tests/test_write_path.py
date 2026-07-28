"""Tests for the decoded write path and the CLI built on it.

These cover the packets that actually reach the monitor, so a regression here
would send malformed frames to hardware we cannot read back from.
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from omenled import cli, device, protocol, transport
from omenled.journal import Journal

from test_transport import FakeHidDevice, fake_hid  # noqa: F401  (fixture reuse)


class TestPlanePacket:
    def test_is_a_full_length_report(self):
        assert len(protocol.plane_packet(protocol.PLANE_RED, [0xFF])) == 64

    def test_carries_the_header_the_device_accepted(self):
        packet = protocol.plane_packet(protocol.PLANE_RED, [])
        assert packet[:8] == bytes([0x05, 0x00, 0x00, 0xD0, 0x00, 0x00, 0x00, 0x0F])

    def test_intensities_begin_at_the_first_data_byte(self):
        packet = protocol.plane_packet(protocol.PLANE_GREEN, [0x11, 0x22])
        assert packet[protocol.DATA_FIRST] == 0x11
        assert packet[protocol.DATA_FIRST + 1] == 0x22

    def test_short_sequences_are_zero_padded(self):
        packet = protocol.plane_packet(protocol.PLANE_BLUE, [0xFF])
        assert set(packet[protocol.DATA_FIRST + 1:]) == {0}

    def test_the_opcode_selects_the_plane(self):
        assert protocol.plane_packet(protocol.PLANE_GREEN, [])[0] == 0x06
        assert protocol.plane_packet(protocol.PLANE_BLUE, [])[0] == 0x07

    def test_the_chunk_index_lands_in_byte_1(self):
        assert protocol.plane_packet(protocol.PLANE_RED, [], chunk=1)[1] == 1

    def test_an_unknown_plane_is_refused(self):
        with pytest.raises(ValueError, match="plane must be one of"):
            protocol.plane_packet(0x99, [])

    def test_too_many_intensities_are_refused(self):
        with pytest.raises(ValueError, match="exceeds"):
            protocol.plane_packet(protocol.PLANE_RED, [0] * (protocol.LED_COUNT + 1))

    def test_out_of_range_intensities_are_refused(self):
        with pytest.raises(ValueError, match="fit in a byte"):
            protocol.plane_packet(protocol.PLANE_RED, [256])

    def test_the_led_count_matches_the_report_geometry(self):
        assert protocol.LED_COUNT == 64 - protocol.DATA_FIRST == 56


class TestFramePackets:
    def test_one_frame_is_three_reports(self):
        assert len(protocol.frame_packets([(1, 2, 3)])) == 3

    def test_each_channel_goes_to_its_own_plane(self):
        red, green, blue = protocol.frame_packets([(0x11, 0x22, 0x33)])
        assert red[0] == protocol.PLANE_RED
        assert red[protocol.DATA_FIRST] == 0x11
        assert green[protocol.DATA_FIRST] == 0x22
        assert blue[protocol.DATA_FIRST] == 0x33

    def test_a_pure_colour_leaves_the_other_planes_dark(self):
        red, green, blue = protocol.frame_packets([(0xFF, 0, 0)] * protocol.LED_COUNT)
        assert set(green[protocol.DATA_FIRST:]) == {0}
        assert set(blue[protocol.DATA_FIRST:]) == {0}

    def test_too_many_pixels_are_refused(self):
        with pytest.raises(ValueError):
            protocol.frame_packets([(0, 0, 0)] * (protocol.LED_COUNT + 1))


class TestOffPacket:
    def test_uses_the_blanking_opcode(self):
        assert protocol.off_packet()[0] == protocol.OP_OFF

    def test_is_a_full_length_report(self):
        assert len(protocol.off_packet()) == 64


class TestParseColour:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("#ff0000", (255, 0, 0)),
            ("ff0000", (255, 0, 0)),
            ("#0f8", (0, 255, 136)),
            ("red", (255, 0, 0)),
            ("GREEN", (0, 255, 0)),
            ("  blue  ", (0, 0, 255)),
            ("off", (0, 0, 0)),
        ],
    )
    def test_accepted_forms(self, text, expected):
        assert protocol.parse_colour(text) == expected

    @pytest.mark.parametrize("text", ["#ff00", "nonsense", "#gggggg", "", "banana"])
    def test_rejected_forms_always_list_what_is_accepted(self, text):
        # "banana" is six characters and reaches the hex path; reporting it as
        # "not hex" would be true and useless.
        with pytest.raises(ValueError) as exc:
            protocol.parse_colour(text)
        assert "#rrggbb" in str(exc.value)
        assert "red" in str(exc.value)


class TestGradient:
    def test_endpoints_are_exact(self):
        ramp = protocol.gradient((0, 0, 0), (255, 0, 0), 5)
        assert ramp[0] == (0, 0, 0)
        assert ramp[-1] == (255, 0, 0)

    def test_length_matches_the_request(self):
        assert len(protocol.gradient((0, 0, 0), (1, 1, 1), 56)) == 56

    def test_a_single_step_is_the_start_colour(self):
        assert protocol.gradient((9, 9, 9), (1, 1, 1), 1) == [(9, 9, 9)]

    def test_zero_steps_is_empty(self):
        assert protocol.gradient((0, 0, 0), (1, 1, 1), 0) == []


@pytest.fixture
def lights(fake_hid, tmp_path):  # noqa: F811
    journal = Journal(session="t", directory=str(tmp_path))
    link = transport.Transport(journal, min_interval=0).open()
    yield device.Lighting(link), fake_hid
    link.close()
    journal.close()


class TestLighting:
    def test_render_sends_exactly_three_reports(self, lights):
        light, hid = lights
        light.fill((255, 0, 0)).render()
        assert len(hid.written) == 3

    def test_every_report_is_65_bytes_on_the_wire(self, lights):
        light, hid = lights
        light.fill((1, 2, 3)).render()
        assert all(len(w) == 65 for w in hid.written)

    def test_set_led_only_lights_that_led(self, lights):
        light, hid = lights
        light.set_led(2, (255, 0, 0)).render()
        red = hid.written[0][1:]
        assert red[protocol.DATA_FIRST + 2] == 255
        assert red[protocol.DATA_FIRST] == 0

    def test_an_out_of_range_led_is_refused(self, lights):
        light, _ = lights
        with pytest.raises(IndexError, match="outside"):
            light.set_led(protocol.LED_COUNT, (1, 1, 1))

    def test_off_sends_one_report_and_clears_the_framebuffer(self, lights):
        light, hid = lights
        light.fill((255, 255, 255)).off()
        assert len(hid.written) == 1
        assert set(light.pixels) == {(0, 0, 0)}

    def test_set_all_pads_short_input_with_darkness(self, lights):
        light, _ = lights
        light.set_all([(1, 1, 1)])
        assert len(light.pixels) == protocol.LED_COUNT
        assert light.pixels[-1] == (0, 0, 0)

    def test_set_all_refuses_more_pixels_than_leds(self, lights):
        light, _ = lights
        with pytest.raises(ValueError):
            light.set_all([(0, 0, 0)] * (protocol.LED_COUNT + 1))

    def test_dry_run_reaches_no_hardware(self, lights):
        light, hid = lights
        light.fill((255, 0, 0)).render(dry_run=True)
        assert hid.written == []


class TestCliParsing:
    def test_colour_arguments_reject_nonsense_with_a_usable_message(self, capsys):
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(["fill", "banana"])
        assert "#rrggbb" in capsys.readouterr().err

    def test_fill_parses_a_hex_colour(self):
        args = cli.build_parser().parse_args(["fill", "#00ff00"])
        assert args.colour == (0, 255, 0)

    def test_set_accepts_several_indices(self):
        args = cli.build_parser().parse_args(["set", "0", "3", "7", "red"])
        assert args.index == [0, 3, 7]
        assert args.colour == (255, 0, 0)

    def test_gradient_takes_two_colours(self):
        args = cli.build_parser().parse_args(["gradient", "red", "blue"])
        assert args.start == (255, 0, 0) and args.end == (0, 0, 255)

    def test_dry_run_is_available_on_every_command(self):
        for argv in (["--dry-run", "off"], ["--dry-run", "fill", "red"]):
            assert cli.build_parser().parse_args(argv).dry_run

    def test_chase_defaults_to_white(self):
        assert cli.build_parser().parse_args(["chase"]).colour == (0xFF, 0xFF, 0xFF)


class TestCliCommands:
    def test_set_blanks_the_others_by_default(self, lights):
        light, hid = lights
        args = cli.build_parser().parse_args(["set", "1", "red"])
        cli.cmd_set(light, args)
        assert light.pixels[0] == (0, 0, 0)
        assert light.pixels[1] == (255, 0, 0)

    def test_keep_preserves_the_rest(self, lights):
        light, _ = lights
        light.fill((0, 0, 255))
        args = cli.build_parser().parse_args(["set", "1", "red", "--keep"])
        cli.cmd_set(light, args)
        assert light.pixels[0] == (0, 0, 255)
        assert light.pixels[1] == (255, 0, 0)

    def test_gradient_spans_every_led(self, lights):
        light, _ = lights
        args = cli.build_parser().parse_args(["gradient", "black", "red"])
        cli.cmd_gradient(light, args)
        assert light.pixels[0] == (0, 0, 0)
        assert light.pixels[-1] == (255, 0, 0)

    def test_chase_dry_run_visits_every_led(self, lights, capsys):
        light, _ = lights
        args = cli.build_parser().parse_args(["--dry-run", "chase", "red"])
        cli.cmd_chase(light, args)
        assert f"LED {protocol.LED_COUNT - 1}" in capsys.readouterr().out


class TestParseReply:
    def test_an_accepted_write_reads_as_ok(self):
        reply = protocol.parse_reply(bytes([0x05, 0x00, 0x00, 0x80, 0xEC, 0xAC]))
        assert reply.ok and reply.opcode == 0x05

    def test_the_rejection_status_is_not_ok(self):
        # Observed on blue-plane writes sent straight after red, skipping green.
        reply = protocol.parse_reply(bytes([0x07, 0x00, 0x00, 0xF0, 0xEC, 0xFA, 0x01]))
        assert not reply.ok
        assert "rejected" in str(reply)

    def test_an_unknown_status_is_not_treated_as_success(self):
        assert not protocol.parse_reply(bytes([0x05, 0, 0, 0x42])).ok

    def test_a_timeout_parses_to_nothing(self):
        assert protocol.parse_reply(None) is None

    def test_a_reply_too_short_to_hold_a_status_parses_to_nothing(self):
        assert protocol.parse_reply(bytes([0x05, 0x00, 0x00])) is None


class TestRenderVerification:
    def test_planes_go_out_in_red_green_blue_order(self, lights):
        light, hid = lights
        light.fill((1, 2, 3)).render(verify=False)
        assert [w[1] for w in hid.written] == [
            protocol.PLANE_RED, protocol.PLANE_GREEN, protocol.PLANE_BLUE]

    def test_a_rejection_raises_rather_than_displaying_a_partial_frame(self, lights):
        light, hid = lights
        hid.replies = [bytes([0x05, 0, 0, protocol.STATUS_ERROR]) + bytes(60)]
        with pytest.raises(device.RenderRejected, match="red plane was rejected"):
            light.fill((255, 0, 0)).render()

    def test_the_error_says_how_to_recover(self, lights):
        light, hid = lights
        hid.replies = [bytes([0x05, 0, 0, protocol.STATUS_ERROR]) + bytes(60)]
        with pytest.raises(device.RenderRejected, match="send `off`"):
            light.fill((255, 0, 0)).render()

    def test_ok_replies_render_all_three_planes(self, lights):
        light, hid = lights
        hid.replies = [bytes([0, 0, 0, protocol.STATUS_OK]) + bytes(60)] * 3
        light.fill((1, 1, 1)).render()
        assert len(hid.written) == 3

    def test_a_timeout_is_not_treated_as_a_rejection(self, lights):
        # The device does not always answer; silence must not abort a render.
        light, hid = lights
        hid.replies = []
        light.fill((1, 1, 1)).render()
        assert len(hid.written) == 3

    def test_verify_false_skips_the_readback(self, lights):
        light, hid = lights
        hid.replies = [bytes([0x05, 0, 0, protocol.STATUS_ERROR]) + bytes(60)]
        light.fill((1, 1, 1)).render(verify=False)  # must not raise
        assert len(hid.written) == 3
