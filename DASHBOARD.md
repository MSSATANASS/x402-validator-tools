# x402 Validator Dashboard

Dashboard web construido con Flask que permite validar endpoints, ver históricos y monitorear tendencias.

## Inicio rápido

```bash
python main.py dashboard
# Abrir http://localhost:5000
```

O con docker-compose:

```bash
docker-compose up dashboard
```

## Vistas

### Home (`/`)

- Formulario para validar una URL
- Doughnut chart con ratio pass/fail de las últimas 50 validaciones
- Tabla con historial (URL, estado, enlace al reporte)

### Reporte (`/report/<id>`)

- Detalle de la validación (URL, timestamp, overall status)
- Tabla de checks individuales
- Bar chart pass/fail por check

### API

- `GET /api/history` — últimos 100 resultados en JSON
- `GET /api/validate/<url>` — validar y devolver JSON

## Datos

Los resultados persisten en `data/results.json`. El archivo se crea automáticamente.
