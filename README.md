# JTAG Web Station

Interfaz web para subir un archivo BSDL desde tu computadora y ejecutar una revision JTAG Boundary Scan en la Raspberry Pi.

## Estructura

```text
backend/   Servidor Flask + script JTAG
frontend/  Web React/Vite
```

## 1. Instalar backend en la Raspberry

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
sudo python3 app.py
```

El backend queda en:

```text
http://IP_DE_TU_RASPBERRY:5000
```

Nota: si `sudo python3 app.py` no usa el venv, puedes instalar Flask globalmente o ejecutar sin venv si ya tienes dependencias instaladas.

## 2. Instalar frontend en la Raspberry

En otra terminal:

```bash
cd frontend
npm install
npm run dev -- --host 0.0.0.0
```

La web queda en:

```text
http://IP_DE_TU_RASPBERRY:5173
```

Desde tu computadora entra a esa direccion.

## 3. Uso

1. Abre la web.
2. Sube el archivo `.bsdl` desde tu computadora.
3. Aprieta **Iniciar revision**.
4. Mira el progreso en vivo.

## Pines JTAG fijos en la Raspberry

El script usa:

```text
TCK = GPIO11
TMS = GPIO25
TDI = GPIO10
TDO = GPIO9
Velocidad = 10 kHz
```

Si cambias el cableado, modifica estos valores en:

```text
backend/mega_jtag_bsdl_test.py
```

## Netlist en la web

Esta version permite subir tambien un archivo Netlist opcional desde la interfaz web.

- Si subes solo BSDL: hace revision general de cortos.
- Si subes BSDL + Netlist: compara los cortos detectados contra el netlist y separa:
  - OK_SEGUN_NETLIST
  - CORTO_SOSPECHOSO
  - NO_MEDIBLE_DIRECTO
  - OPEN_POSIBLE / BRIDGE_POSIBLE / MIXTO cuando se puede medir directo.

El backend ejecuta:

```bash
sudo python3 -u mega_jtag_bsdl_netlist_test.py archivo.bsdl archivo.net --uut-ref U1 --netlist-test
```
