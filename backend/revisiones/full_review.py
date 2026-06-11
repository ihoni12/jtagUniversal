
def review_all(sock, tap, extest, sample_opcode, bits, pins, board_map=None, nets=None, uut_ref=None, external_line_test=False, external_bidir=False, pi_chip="0"):
    from jtag_tester_core import run_short_test, run_netlist_test, build_external_line_tests, run_external_line_tests

    short_results = run_short_test(sock, tap, extest, sample_opcode, bits, pins, board_map)
    net_report = run_netlist_test(sock, tap, extest, sample_opcode, bits, pins, board_map) if board_map else None
    external_report = None
    if external_line_test and nets and uut_ref:
        external_tests = build_external_line_tests(nets, pins, uut_ref, external_bidir=external_bidir)
        external_report = run_external_line_tests(sock, tap, extest, sample_opcode, bits, pins, external_tests, pi_chip=pi_chip)
    return {"short_results": short_results, "net_report": net_report, "external_report": external_report}
