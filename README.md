# JTAG Universal Test Station

Sistema web local para Raspberry Pi.

## Qué hace

- Sube BSDL.
- Extrae entity, IDCODE, IR length, opcodes, Boundary Register y pines.
- Genera plan automático de pruebas por pin.
- Ejecuta OpenOCD automáticamente con configuración creada desde el BSDL.
- Prueba no invasiva: scan_chain, IDCODE y SAMPLE.
- Prueba opcional EXTEST para buscar posibles cortos manejando pines.
- Prueba funcional con firmware: carga firmware y espera mensaje TCP desde la placa.

## Arranque en Raspberry Pi

Terminal 1:

```bash
cd ~/jtagUniversal
./start_backend.sh
```

Terminal 2:

```bash
cd ~/jtagUniversal
./start_frontend.sh
```

Abrir:

```text
http://IP_DE_LA_PI:5173
```

## Importante

Con solo BSDL el sistema puede revisar JTAG, IDCODE, Boundary Scan, pines y posibles cortos.
Para saber qué pines deben estar conectados en la placa, hace falta Netlist.
