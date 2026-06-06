LECTOR SIMPLE DE BSDL
======================

Este programa recibe un archivo .bsdl y muestra la informacion separada:

1) CHIP / ENTITY
2) PUERTOS / PINES DECLARADOS
3) INSTRUCCIONES JTAG
4) IDCODE
5) BOUNDARY REGISTER
6) OTROS ATRIBUTOS IMPORTANTES

COMO USARLO
===========

1) Entra a la carpeta:
   cd bsdl_reader

2) Ejecuta:
   python bsdl_reader.py tu_archivo.bsdl

Ejemplo:
   python bsdl_reader.py atmega2560.bsdl

En Raspberry Pi normalmente tambien funciona asi:
   python3 bsdl_reader.py atmega2560.bsdl

NOTA
====
Este programa solo lee y separa la informacion del BSDL.
No conecta al chip, no hace JTAG, no revisa cortos.
PYTHON NECESARIO: Python 3.
