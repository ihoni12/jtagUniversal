# Cambios UART completo

- Agregada detección de parejas UART desde el netlist usando nombres como `NET_UART0_TX` y `NET_UART0_RX`.
- En cada pin TX/RX aparece la opción **Revisar UART completo**.
- La interfaz recomienda automáticamente el pin opuesto del mismo UART: TX0 con RX0, TX1 con RX1.
- También permite elegir manualmente otro pin opuesto, pero muestra advertencia si no es del mismo UART.
- Nuevo endpoint backend: `/api/start-uart-pair`.
- Nuevo reporte: `uart_pair_report.json`.

Nota: esta revisión es eléctrica con JTAG + GPIO de Raspberry. Verifica que las dos líneas TX/RX responden como pareja, pero no configura baud rate ni transmite bytes UART reales desde firmware.
