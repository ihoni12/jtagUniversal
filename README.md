# BSDL JTAG Chip Tester

Sistema simple para Raspberry Pi:
- Sube BSDL
- Analiza IDCODE, IR, Boundary Register y pines
- Ejecuta OpenOCD
- Revisa IDCODE + SAMPLE
- Opcional: EXTEST para posibles cortos

## Uso

Terminal 1:

```bash
./start_backend.sh
```

Terminal 2:

```bash
./start_frontend.sh
```

Abrir:

```text
http://IP_DE_LA_PI:5173
```

## Importante

Con solo BSDL se revisa el chip y los pines Boundary Scan.
Para saber conexiones exactas de la placa se necesita Netlist.
