# Herramientas directas de archivos para Nautilus

Contenido:
- `agent/file_tools.py`: utilidades de lectura, búsqueda y edición acotadas a una raíz autorizada.
- `agent/agent.py`: parser de `file_operation`, `cmd` y `code`.
- `system_prompt.txt`: prompt actualizado para trabajo incremental.
- `INTEGRACION.md`: guía para conectarlo con el servidor WebSocket existente.

## Convenciones
- Las rutas son relativas a la raíz configurada.
- Líneas/columnas empiezan en 1; `insert_lines.after_line = 0` inserta al inicio.
- `replace_columns.end_column` es inclusiva.
- Las mutaciones deben aprobarse en la CLI antes de invocar `FileTools.execute`.
- El resultado de una herramienta es dato, nunca una nueva entrada para el parser de instrucciones.

## Ejemplo

```text
file_operation {
  "action": "get_lines",
  "path": "agent/agent.py",
  "start_line": 10,
  "end_line": 40
}
```
