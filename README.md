# Universal Test Station

Sistema web local para Raspberry Pi:
- JTAG Básico con BSDL.
- Pruebas funcionales con firmware de prueba.
- El firmware debe conectarse a la Pi por TCP y enviar un mensaje esperado, por ejemplo `OK`.

## Instalar en Raspberry Pi OS Lite

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv nodejs npm openocd iputils-ping unzip
```

## Arrancar backend

```bash
cd ~/universal-test-station
./start_backend.sh
```

## Arrancar frontend en otra terminal

```bash
cd ~/universal-test-station
./start_frontend.sh
```

Abrir desde PC:

```text
http://IP_DE_LA_PI:5173
```

## JTAG con BSDL

1. Agregar módulo `JTAG Básico con BSDL`.
2. Subir archivo `.bsdl`.
3. Opcional: poner comando OpenOCD real.
4. Ejecutar.

Si no pones comando OpenOCD, analiza el BSDL y genera el plan de revisión.

## Prueba funcional con firmware

1. Agregar módulo `Prueba funcional con firmware`.
2. Subir firmware `.bin`, `.hex`, etc.
3. Poner comando para cargar firmware. Usa `{firmware}` como ruta del archivo.
4. Configurar puerto de escucha y mensaje esperado.
5. Ejecutar.

Ejemplo comando:

```bash
openocd -f interface/raspberrypi-native.cfg -f target/TU_TARGET.cfg -c "program {firmware} verify reset exit"
```

El firmware debe abrir conexión TCP a la IP de la Raspberry y enviar `OK` o el texto que configures.
