LECTOR BSDL CON CLIENTE WEB
===========================

Este paquete tiene 2 formas de uso:

1) Modo consola:
   python3 bsdl_reader.py archivo.bsdl

2) Modo web / cliente desde otra computadora:
   python3 web_server.py

Luego abre desde tu computadora:
   http://IP_DE_TU_RASPBERRY:8088

Ejemplo:
   http://192.168.1.50:8088

El programa imprime en la terminal la direccion correcta cuando inicia.

Si quieres otro puerto:
   python3 web_server.py 8090

Notas:
- No necesita Flask ni instalar librerias externas.
- Solo usa Python 3.
- Solo lee y separa informacion del archivo BSDL.
- No conecta al chip todavia.

Si no abre desde tu computadora:
1. Verifica que Raspberry y computadora esten en la misma red.
2. Mira la IP con:
   hostname -I
3. Prueba en la Raspberry:
   curl http://127.0.0.1:8088
