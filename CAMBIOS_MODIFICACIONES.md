# Cambios agregados

## Frontend
- Barra lateral izquierda plegable.
- La barra muestra el nombre del chip/placa y todos los pines sacados del BSDL/netlist.
- Buscador de pines por nombre, net o función detectada.
- Al tocar un pin aparece un panel con información del pin: bits BSDL, funciones probables y nets.
- Botón **Probar este pin** para ejecutar una revisión física sólo de ese pin.
- Botón **Analizar archivos / cargar pines** para leer BSDL/netlist sin usar JTAG.
- Botón **Iniciar revisión completa** mantiene la revisión completa anterior.

## Backend
- Nuevo endpoint `POST /api/analyze` para leer BSDL/netlist y devolver mapa de pines.
- Nuevo endpoint `POST /api/start-pin` para revisar un pin individual.
- Nueva carpeta `backend/revisiones/` con lógica separada:
  - `pin_review.py`: revisión de un pin individual.
  - `tx_rx_review.py`: revisión de TX/RX usando conexiones PI.GPIOxx del netlist.
  - `full_review.py`: modelo para revisión completa reutilizando funciones.
- Nuevo archivo `backend/run_everything_review.py` para ejecutar todo lo revisable desde consola.

## Uso rápido
Backend:
```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
sudo python3 app.py
```

Frontend:
```bash
cd frontend
npm install
npm run dev -- --host 0.0.0.0
```

Desde consola, revisión completa:
```bash
cd backend
sudo python3 run_everything_review.py archivo.bsdl archivo.net --uut-ref U1 --out reportes
```
