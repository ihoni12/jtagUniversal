Lector BSDL - paso 1

Este programa solo lee un archivo BSDL y muestra la informacion separada:
1. Informacion general
2. Instrucciones JTAG
3. Pines y sus celdas
4. Registro boundary completo
5. Avisos

Instalacion en Raspberry Pi:

python3 -m venv venv
source venv/bin/activate
pip install flask
python3 app.py

Abrir en navegador:
http://IP_DE_TU_PI:8088

No hace revision JTAG todavia. Este es solo el paso de lectura del BSDL.
