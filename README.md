# JTAG BSDL Tester para Raspberry Pi + ATmega2560

Este programa ya no solo lee el BSDL. Ahora hace:

1. Verificación real de conexión JTAG con `IDCODE`.
2. Lectura `SAMPLE`.
3. Revisión `EXTEST` diferencial para detectar cortos sospechosos.

## Instalar

```bash
cd ~/jtagUniversal
python3 -m venv venv
source venv/bin/activate
pip install flask
```

## Arrancar OpenOCD

En una terminal:

```bash
sudo openocd -f pi-atmega2560.cfg
```

Debe leer IDCODE `0x4980103f`. Si sale `all zeroes`, el problema es cableado/alimentación/fuse JTAG, no el programa.

Cables usados por `pi-atmega2560.cfg`:

```text
Raspberry GPIO11 pin físico 23 -> ATmega TCK / PF4
Raspberry GPIO8  pin físico 24 -> ATmega TMS / PF5
Raspberry GPIO10 pin físico 19 -> ATmega TDI / PF7
Raspberry GPIO9  pin físico 21 -> ATmega TDO / PF6
Raspberry GND                  -> Arduino GND
Arduino debe estar alimentado
```

## Arrancar la interfaz

En otra terminal:

```bash
source venv/bin/activate
python3 app.py
```

Abrir:

```text
http://IP_DE_TU_PI:8088
```

## Uso recomendado

1. Subir el BSDL corregido.
2. Acción: `1. Verificar conexión IDCODE`.
3. Si da OK, acción: `2. Leer SAMPLE`.
4. Si da OK, acción: `3. Revisar cortos sospechosos EXTEST`.

## Importante

Sin netlist de la placa, el programa solo puede decir `POSIBLE CORTO`. No sabe si dos pines están conectados intencionalmente.
