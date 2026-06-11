
def review_tx_rx(sock, tap, extest, sample_opcode, bits, pins, nets, uut_ref, external_bidir=False, pi_chip="0"):
    from jtag_tester_core import build_external_line_tests, run_external_line_tests

    tests = build_external_line_tests(nets, pins, uut_ref, external_bidir=external_bidir)
    wanted = []
    for item in tests:
        net = item.get("net", "").upper()
        direction = item.get("direction", "").upper()
        if "TX" in net or "RX" in net or "UART" in net or "TX" in direction or "RX" in direction:
            wanted.append(item)
    return run_external_line_tests(sock, tap, extest, sample_opcode, bits, pins, wanted, pi_chip=pi_chip)
