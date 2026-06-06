# JTAG BSDL Tester para Raspberry Pi

Programa simple para subir un archivo BSDL, leer pines/celdas boundary scan y ejecutar pruebas básicas usando OpenOCD por telnet.

## Instalar

```bash
sudo apt update
sudo apt install -y python3 python3-pip openocd
cd jtag_bsdl_tester
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Arrancar OpenOCD

Ejemplo para Raspberry Pi native JTAG:

```bash
sudo openocd -f interface/raspberrypi-native.cfg -f target/atmega2560.cfg
```

Si tu target se llama diferente, mira el nombre con:

```bash
echo 'scan_chain' | nc localhost 4444
```

Pon ese nombre en la interfaz, por ejemplo `atmega2560.cpu`.

## Ejecutar la interfaz

```bash
source venv/bin/activate
python3 app.py
```

Abre en el navegador:

```text
http://IP_DEL_PI:8088
```

## Qué revisa

1. Lee el BSDL.
2. Encuentra pines, longitud IR, longitud boundary register e instrucciones como IDCODE, SAMPLE, EXTEST.
3. SAMPLE: lee el estado actual de los pines.
4. EXTEST: intenta manejar un pin a 0/1 y observa otros pines para marcar posibles cortos.

## Importante

Solo con BSDL no se puede saber si dos pines deben estar conectados normalmente en tu placa. Para una revisión profesional necesitas también el netlist de la placa. Sin netlist, el programa solo puede marcar “sospechas”, no decir 100% que es un corto.

No uses EXTEST en una placa conectada a otros circuitos activos o fuentes externas sin entender el riesgo, porque puede pelear contra otra salida y dañar algo.
