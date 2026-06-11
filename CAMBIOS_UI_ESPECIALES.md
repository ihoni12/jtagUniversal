Cambios nuevos:
- Lista de pines comprimida aproximadamente a la mitad de alto.
- Scroll de pines más limpio y visible.
- Pines especiales marcados al lado: TX, RX, I2C, SPI.
- Panel de pin muestra si tiene conexión PI.GPIOxx según netlist.
- Botón extra para probar conexión especial del pin seleccionado.
- Backend agrega endpoint /api/start-special-pin para revisar solo esa conexión externa.

Nota:
Para que TX/RX se pueda probar como conexión, el netlist debe incluir una relación como:
NET_UART_TX U1.PE1 PI.GPIO15
NET_UART_RX U1.PE0 PI.GPIO14
