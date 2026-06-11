from collections import defaultdict


def classify_pin_result(pin, raw_result, board_map=None):
    from jtag_tester_core import build_pin_net_lookup

    pin_to_nets, same_net_pins = build_pin_net_lookup(board_map)
    followers = set(raw_result.get("followers", []))
    allowed = set(same_net_pins.get(pin, set()))
    expected_followers = sorted(followers & allowed)
    unexpected_followers = sorted(followers - allowed)

    stuck_at_0 = bool(raw_result.get("stuck_at_0"))
    stuck_at_1 = bool(raw_result.get("stuck_at_1"))

    if stuck_at_0:
        status = "STUCK_AT_0"
        passed = False
    elif stuck_at_1:
        status = "STUCK_AT_1"
        passed = False
    elif unexpected_followers:
        status = "CORTO_SOSPECHOSO"
        passed = False
    else:
        status = "OK_SEGUN_NETLIST" if expected_followers else "OK_SIN_CORTO"
        passed = True

    return {
        "pin": pin,
        "passed": passed,
        "status": status,
        "followers": sorted(followers),
        "expected_followers_by_netlist": sorted(allowed),
        "allowed_detected_followers": expected_followers,
        "unexpected_followers": unexpected_followers,
        "driver_nets": sorted(pin_to_nets.get(pin, [])),
        "unexpected_follower_nets": {
            other: sorted(pin_to_nets.get(other, [])) for other in unexpected_followers
        },
        "stuck_at_0": stuck_at_0,
        "stuck_at_1": stuck_at_1,
        "selected_high_read": raw_result.get("selected_high_read"),
        "selected_low_read": raw_result.get("selected_low_read"),
        "raw": raw_result,
    }


def review_pin(sock, tap, extest, sample_opcode, bits, pins, pin, board_map=None):
    from jtag_tester_core import test_one_pin

    pin = str(pin).upper()
    if pin not in pins:
        raise ValueError(f"El pin {pin} no existe en el BSDL o no es controlable")
    raw = test_one_pin(sock, tap, extest, sample_opcode, bits, pins, pin)
    return classify_pin_result(pin, raw, board_map=board_map)
