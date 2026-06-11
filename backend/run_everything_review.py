#!/usr/bin/env python3
"""Ejecuta todo lo revisable: cortos, netlist y líneas externas TX/RX/SPI/I2C/GPIO.
Uso:
  sudo python3 run_everything_review.py archivo.bsdl archivo.net --uut-ref U1
"""
import sys
from jtag_tester_core import main

if __name__ == "__main__":
    # Este wrapper existe para tener un archivo claro de "revisar todo".
    # Internamente usa el tester principal con todas las opciones importantes.
    args = sys.argv[1:]
    if not args:
        print("Uso: sudo python3 run_everything_review.py archivo.bsdl [archivo.net] --uut-ref U1")
        sys.exit(1)
    if "--netlist-test" not in args:
        args.append("--netlist-test")
    if "--external-line-test" not in args:
        args.append("--external-line-test")
    old = sys.argv
    sys.argv = [old[0]] + args
    main()
