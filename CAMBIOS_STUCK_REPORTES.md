# Cambios agregados

- Revisión Stuck-at-0: detecta si un pin queda pegado a 0 cuando se intenta poner en 1.
- Revisión Stuck-at-1: detecta si un pin queda pegado a 1 cuando se intenta poner en 0.
- Integrado en revisión completa de pines normales.
- Integrado en revisión individual de pin.
- Integrado en revisión de conexión especial y UART completo con informe por pin.
- Consola muestra el progreso de cambio 0/1, stuck-at-0, stuck-at-1 y cortos.
- Informe final más simple: diferencia entre pines disponibles en BSDL y pines usados en la prueba.
- Informe avanzado por pin/conexión con PASS/FAIL/ERROR y motivo entendible.
- Resumen de problemas por tipo: pegados a 0, pegados a 1, cortos, abiertos y errores.
