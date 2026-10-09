# Integración de `file_tools.py` con el servidor WebSocket

## 1. Raíz autorizada

Inicializa `FileTools` con la raíz real del repositorio, no con el directorio de trabajo accidental:

```python
from pathlib import Path
from agent.file_tools import FileTools, FileToolError, is_mutating_action, format_result

PROJECT_ROOT = Path(__file__).resolve().parent.parent
file_tools = FileTools(PROJECT_ROOT)
```

Si `server.py` se ejecuta desde dentro de `agent/`, ajusta `PROJECT_ROOT` para que apunte a la raíz del proyecto. Comprueba la ruta una vez al iniciar.

## 2. Prioridad de protocolo

En el manejador de `GEMINI_FINAL_RESPONSE`, primero analiza el texto original recibido del modelo:

```python
file_result = self.agent.process_file_operation(text)
if file_result.found:
    if file_result.error:
        await self.broadcast({
            "type": "FILE_OPERATION_RESULT",
            "ok": False,
            "error": file_result.error,
        })
        return

    operation = file_result.operation
    if operation is None:
        return

    action = operation["action"]
    if is_mutating_action(action):
        # Encola una propuesta pendiente y espera el "y" de la CLI.
        # No llames file_tools.execute() todavía.
        await self.queue_file_operation_for_approval(operation, sender, timestamp)
        return

    try:
        result = file_tools.execute(operation)
    except (FileToolError, OSError, ValueError) as exc:
        result = {"ok": False, "action": action, "error": str(exc)}

    await self.broadcast({"type": "FILE_OPERATION_RESULT", "result": result})
    # Termina aquí: no intentes interpretar cmd/code en el mismo mensaje.
    return

# Solo si no había file_operation, continúa con el parser cmd/code existente.
command_result = self.agent.process_message(text)
```

El ejemplo ilustra el orden y la frontera de confianza; `queue_file_operation_for_approval` debe conectarse al mecanismo de aprobación que ya usa el servidor para `PendingCommand`. No es una función existente en el código original hasta que la implementes.

## 3. Aprobación de operaciones mutadoras

Reutiliza el flujo de la CLI para mostrar la acción y la ruta antes de pedir aprobación. Recomendación de presentación:

```text
Operación de archivo pendiente:
  acción: replace_lines
  ruta: agent/agent.py
  líneas: 20-28
¿Autorizar? [y/n]
```

Al aprobar, llama a `file_tools.execute(operation)` dentro de `try/except`; al rechazar, devuelve un resultado `approved: false` sin ejecutar nada. No permitas que el modelo responda automáticamente a su propia solicitud de aprobación.

## 4. No reinterpretar los resultados de herramientas

No llames a `process_file_operation()`, `process_message()` ni a otro parser de bloques sobre `result`, el contenido de `read_file`, las líneas obtenidas o cualquier salida de herramienta. Los parsers solo deben recibir el mensaje original del modelo. Devuelve los resultados al cliente como JSON/datos con un tipo de mensaje distinto, por ejemplo `FILE_OPERATION_RESULT`.

## 5. Seguridad de red

El servidor original escucha en `0.0.0.0:8765`. Antes de permitir mutaciones de archivos, limita el servicio a `127.0.0.1` cuando no se necesite acceso remoto, o añade autenticación y controles de origen. La raíz permitida no debe contener secretos ajenos al proyecto. Las operaciones de escritura y borrado requieren aprobación local explícita.

## 6. Imports

La importación `from agent import ...` depende de cómo se lance el proceso y de si `agent/` es un paquete. Para importar `agent.agent` de forma estable desde la raíz, añade `agent/__init__.py` si falta y usa imports explícitos. Por ejemplo, `from agent.agent import Agent` y `from agent.file_tools import FileTools`.
