# Estación JTAG Universal

Frontend + backend para Raspberry Pi con OpenOCD. Permite subir BSDL y netlist desde el navegador, ejecutar revisión JTAG y ver una salida simple.

## Qué revisa

- Cortos generales entre pines Boundary Scan.
- Conexiones permitidas según netlist.
- Líneas externas hacia Raspberry Pi definidas como `PI.GPIOxx`.
- UART, SPI, I2C y GPIO simples se detectan por nombre de net: `TX`, `RX`, `MOSI`, `MISO`, `SCK`, `SCL`, `SDA`, `CS`, etc.

## Ejecutar backend

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
sudo ./start_backend.sh
```

O directo:

```bash
cd backend
sudo python3 app.py
```

## Ejecutar frontend

```bash
cd frontend
npm install
npm run dev -- --host 0.0.0.0
```

Abre desde tu computadora:

```text
http://IP_DE_LA_PI:5173
```

## Netlist simple

```text
NET_UART0_TX
  U1.PE1
  PI.GPIO15

NET_UART0_RX
  U1.PE0
  PI.GPIO14
```

## Modo terminal sin frontend

```bash
cd backend
sudo python3 mega_jtag_bsdl_netlist_test.py ../ATMEGA2560_table28_1_CORREGIDO\ \(1\).bsdl ../examples/test_shorts_expected.net --external-line-test --netlist-test
```

## Nota importante

Para revisar una línea externa, tiene que existir cable físico entre el pin del UUT y el GPIO de la Raspberry. El netlist solo le dice al programa qué debe esperar.


## Líneas externas visibles
En el frontend aparece una caja llamada **Líneas externas detectadas**.
Ahí se muestran automáticamente las nets del netlist que tengan U1.PIN conectado a PI.GPIOxx.
Ejemplos:

```net
NET_UART0_TX
  U1.PE1
  PI.GPIO15

NET_UART0_RX
  U1.PE0
  PI.GPIO14

NET_SPI_MOSI
  U1.PB2
  PI.GPIO10

NET_I2C_SCL
  U1.PD0
  PI.GPIO3
```

Direcciones automáticas:
- TX, MOSI, SCK, CLK, CS, SCL: UUT_TO_PI.
- RX, MISO: PI_TO_UUT.
- Con `Probar ambas direcciones`, prueba ambas.
