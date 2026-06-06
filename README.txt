JTAG ATmega2560 pin color tester

1) Terminal 1:
   cd ~/jtagUniversal
   sudo openocd -f ./pi-atmega2560.cfg

2) Terminal 2:
   cd ~/jtagUniversal
   source venv/bin/activate
   pip install flask
   python3 app.py

3) Abrir:
   http://IP_DE_TU_PI:8088

Usa TAP: atmega2560.cpu
Sube el BSDL FULL_BOUNDARY.

Colores:
- Verde: pin probado y no vio otro pin cambiar junto con él.
- Rojo: posible corto/fallo porque otro pin cambió junto con el pin probado.
- Naranja: pin no probado porque no tiene celda data/control suficiente.

Nota: Sin netlist esto detecta sospechas, no confirma 100% todas las conexiones correctas.
