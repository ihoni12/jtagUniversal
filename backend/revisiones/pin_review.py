from collections import defaultdict


def _net_context(pin, raw_result, board_map=None):
    from jtag_tester_core import build_pin_net_lookup
    pin_to_nets, same_net_pins = build_pin_net_lookup(board_map)
    followers = set(raw_result.get("followers", []))
    allowed = set(same_net_pins.get(pin, set()))
    expected_followers = sorted(followers & allowed)
    unexpected_followers = sorted(followers - allowed)
    missing_expected = sorted(allowed - followers)
    return pin_to_nets, followers, allowed, expected_followers, unexpected_followers, missing_expected


def revisar_cambio_01(pin, raw_result):
    """Revisa que el pin pueda subir a 1 y bajar a 0."""
    high = raw_result.get("selected_high_read")
    low = raw_result.get("selected_low_read")
    ok = high == 1 and low == 0
    return {
        "name": "Cambio 0/1",
        "status": "PASS" if ok else "FAIL",
        "message": f"Pedí 1 y leyó {high}; pedí 0 y leyó {low}.",
    }


def revisar_pegado_a_0(pin, raw_result):
    """Detecta si al pedir 1 el pin sigue leyendo 0."""
    fail = bool(raw_result.get("stuck_at_0"))
    return {
        "name": "Pegado a 0",
        "status": "FAIL" if fail else "PASS",
        "message": "Se pidió nivel 1 y el pin no subió." if fail else "El pin sí sube a 1.",
    }


def revisar_pegado_a_1(pin, raw_result):
    """Detecta si al pedir 0 el pin sigue leyendo 1."""
    fail = bool(raw_result.get("stuck_at_1"))
    return {
        "name": "Pegado a 1",
        "status": "FAIL" if fail else "PASS",
        "message": "Se pidió nivel 0 y el pin no bajó." if fail else "El pin sí baja a 0.",
    }


def revisar_corto(pin, raw_result, board_map=None):
    """Detecta pines que siguen al pin probado y que NO están permitidos por el netlist."""
    _pin_to_nets, _followers, _allowed, expected_followers, unexpected_followers, _missing = _net_context(pin, raw_result, board_map)
    fail = bool(unexpected_followers)
    return {
        "name": "Corto circuito",
        "status": "FAIL" if fail else "PASS",
        "unexpected_followers": unexpected_followers,
        "allowed_detected_followers": expected_followers,
        "message": ("Posible corto con: " + ", ".join(unexpected_followers)) if fail else "No hay pines extra siguiendo la señal.",
    }


def revisar_abierto(pin, raw_result, board_map=None):
    """Detecta posible abierto sólo cuando el netlist espera otro pin JTAG en la misma red."""
    _pin_to_nets, _followers, allowed, expected_followers, _unexpected, missing_expected = _net_context(pin, raw_result, board_map)
    # Si no hay otro pin JTAG esperado por netlist, no se puede decidir abierto desde sólo este pin.
    if not allowed:
        return {
            "name": "Abierto",
            "status": "SKIP",
            "missing": [],
            "message": "No hay otro pin JTAG esperado en la misma red; abierto se revisa con netlist o conexión externa.",
        }
    fail = bool(missing_expected)
    return {
        "name": "Abierto",
        "status": "FAIL" if fail else "PASS",
        "missing": missing_expected,
        "message": ("La señal no llegó a: " + ", ".join(missing_expected)) if fail else "La conexión esperada por netlist respondió.",
    }


def clasificar_resultado_pin(pin, raw_result, board_map=None):
    pin_to_nets, followers, allowed, expected_followers, unexpected_followers, missing_expected = _net_context(pin, raw_result, board_map)

    checks = [
        revisar_cambio_01(pin, raw_result),
        revisar_pegado_a_0(pin, raw_result),
        revisar_pegado_a_1(pin, raw_result),
        revisar_corto(pin, raw_result, board_map),
        revisar_abierto(pin, raw_result, board_map),
    ]

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
    elif allowed and missing_expected:
        status = "OPEN_POSIBLE"
        passed = False
    else:
        status = "OK_SEGUN_NETLIST" if expected_followers else "OK_SIN_CORTO"
        passed = True

    return {
        "pin": pin,
        "passed": passed,
        "status": status,
        "checks": checks,
        "followers": sorted(followers),
        "expected_followers_by_netlist": sorted(allowed),
        "allowed_detected_followers": expected_followers,
        "unexpected_followers": unexpected_followers,
        "missing": missing_expected if allowed else [],
        "driver_nets": sorted(pin_to_nets.get(pin, [])),
        "unexpected_follower_nets": {other: sorted(pin_to_nets.get(other, [])) for other in unexpected_followers},
        "stuck_at_0": stuck_at_0,
        "stuck_at_1": stuck_at_1,
        "selected_high_read": raw_result.get("selected_high_read"),
        "selected_low_read": raw_result.get("selected_low_read"),
        "raw": raw_result,
    }


# Alias viejo para no romper imports anteriores.
classify_pin_result = clasificar_resultado_pin


def review_pin(sock, tap, extest, sample_opcode, bits, pins, pin, board_map=None):
    """Función principal de un pin.

    Esta función pide el pin y llama internamente a todas las revisiones:
    cambio 0/1, pegado a 0, pegado a 1, corto circuito y abierto.
    """
    from jtag_tester_core import test_one_pin

    pin = str(pin).upper()
    if pin not in pins:
        raise ValueError(f"El pin {pin} no existe en el BSDL o no es controlable")
    raw = test_one_pin(sock, tap, extest, sample_opcode, bits, pins, pin)
    return clasificar_resultado_pin(pin, raw, board_map=board_map)
