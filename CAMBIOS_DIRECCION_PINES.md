# Cambios: detección de dirección de pines

Se agregó detección para no marcar falsos errores en pines de entrada.

## Qué cambia

- Si el netlist indica una línea de entrada del chip, por ejemplo `NET_UART0_RX` o `MISO`, el programa no fuerza el pin desde JTAG.
- Para entradas, la prueba correcta es: Raspberry pone el GPIO en 0/1 y JTAG lee el pin.
- Si el pin parece entrada pero no hay `PI.GPIOxx` en el netlist, se muestra un aviso claro: no es comprobable sin activador externo.
- En UART completo, TX y RX se prueban según su dirección real:
  - TX: JTAG maneja el pin y Raspberry lee.
  - RX: Raspberry maneja el GPIO y JTAG lee.
- El informe ahora puede mostrar `AVISO` además de PASS/FAIL/ERROR.

## Ejemplo esperado

Para:

```text
NET_UART0_TX
  U1.PE1
  PI.GPIO15

NET_UART0_RX
  U1.PE0
  PI.GPIO14
```

La revisión hace:

- PE1 / TX: JTAG cambia 0/1, Raspberry lee GPIO15.
- PE0 / RX: Raspberry cambia GPIO14, JTAG lee PE0.

Así se evita el falso `pegado a 1` en pines de entrada.
