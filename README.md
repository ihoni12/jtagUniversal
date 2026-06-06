# BSDL Test Station

Cliente web + backend para subir archivos BSDL y ver la información separada.

## Raspberry Pi

Terminal 1:
```bash
cd bsdl-test-station
./start_backend.sh
```

Terminal 2:
```bash
cd bsdl-test-station
./start_frontend.sh
```

Abre desde tu computadora:
```text
http://IP_DE_TU_RASPBERRY:5173
```

El backend queda en:
```text
http://IP_DE_TU_RASPBERRY:8000
```

## Qué muestra
- Entity / chip
- Instruction length
- IDCODE
- Boundary length
- Puertos
- PIN_MAP
- Instrucciones JTAG y opcode
- Boundary register completo
- JSON completo

Esto solo analiza el archivo BSDL. Para revisar cortos o pines reales hace falta ejecutar JTAG real con OpenOCD u otra herramienta.
