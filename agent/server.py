"""Servidor WebSocket con CLI, aprobación local y ejecución controlada."""

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aioconsole
import websockets
from websockets.exceptions import ConnectionClosed

from agent import Agent, CommandParseResult, FileOperationParseResult, extract_plain_text
from file_tools import FileToolError, FileTools, format_result, is_mutating_action

# ============================================================
# CONFIGURACIÓN
# ============================================================

HOST = "0.0.0.0"
PORT = 8765
COMMAND_TIMEOUT_SECONDS = 60
MAX_OUTPUT_CHARS = 50_000

MESSAGE_FINAL_RESPONSE = "GEMINI_FINAL_RESPONSE"
MESSAGE_COMMAND_PROPOSAL = "COMMAND_PROPOSAL"
MESSAGE_COMMAND_RESULT = "COMMAND_RESULT"
MESSAGE_FILE_OPERATION_RESULT = "FILE_OPERATION_RESULT"
MESSAGE_CLI_PROMPT = "CLI_PROMPT"
MESSAGE_SERVER_STATUS = "SERVER_STATUS"

logger = logging.getLogger("server")


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s | %(levelname)-8s | "
            "%(name)s | %(message)s"
        ),
        datefmt="%Y-%m-%d %H:%M:%S",
    )


# ============================================================
# MODELOS
# ============================================================

@dataclass(frozen=True)
class PendingCommand:
    """Comando pendiente de aprobación humana."""

    command: str
    sender: str
    timestamp: str


@dataclass(frozen=True)
class PendingFileOperation:
    """Operación mutadora de archivos pendiente de aprobación humana."""

    operation: dict[str, Any]
    sender: str
    timestamp: str


# ============================================================
# SERVIDOR WEBSOCKET
# ============================================================

class WebSocketServer:
    """Administra conexiones y distribuye mensajes a los clientes."""

    def __init__(self, agent: Agent, file_tools: FileTools) -> None:
        self.agent = agent
        self.file_tools = file_tools
        self.clients: set[Any] = set()
        self.pending_commands: asyncio.Queue[PendingCommand] = asyncio.Queue()
        self.pending_file_ops: asyncio.Queue[PendingFileOperation] = asyncio.Queue()
        self.broadcast_lock = asyncio.Lock()
        self.command_lock = asyncio.Lock()
        self.stopping = False

    async def register_client(self, websocket: Any) -> None:
        self.clients.add(websocket)
        logger.info("Cliente conectado: %s", websocket.remote_address)

        await self.send_to_client(
            websocket,
            {
                "type": MESSAGE_SERVER_STATUS,
                "connected": True,
                "message": "Conectado al servidor del agente.",
            },
        )

    async def unregister_client(self, websocket: Any) -> None:
        self.clients.discard(websocket)
        logger.info("Cliente desconectado: %s", websocket.remote_address)

    async def send_to_client(
        self,
        websocket: Any,
        payload: dict[str, Any],
    ) -> bool:
        """Envía un mensaje a un cliente concreto."""
        try:
            await websocket.send(
                json.dumps(payload, ensure_ascii=False)
            )
            return True
        except (ConnectionClosed, OSError):
            self.clients.discard(websocket)
            logger.warning(
                "No se pudo enviar un mensaje al cliente desconectado."
            )
            return False

    async def broadcast(self, payload: dict[str, Any]) -> None:
        """Distribuye un mensaje a todos los clientes conectados."""
        async with self.broadcast_lock:
            if not self.clients:
                logger.debug(
                    "No hay clientes conectados para recibir el mensaje."
                )
                return

            clients = tuple(self.clients)

            results = await asyncio.gather(
                *(
                    self.send_to_client(client, payload)
                    for client in clients
                ),
                return_exceptions=True,
            )

            for result in results:
                if isinstance(result, Exception):
                    logger.error(
                        "Error durante una difusión WebSocket: %s",
                        result,
                    )

    async def handle_client(self, websocket: Any) -> None:
        await self.register_client(websocket)

        try:
            async for raw_message in websocket:
                await self.handle_message(raw_message, websocket)

        except ConnectionClosed as error:
            logger.info(
                "Conexión cerrada: código=%s",
                error.code,
            )
        except Exception:
            logger.exception("Error inesperado atendiendo al cliente.")
        finally:
            await self.unregister_client(websocket)

    async def handle_message(
        self,
        raw_message: str,
        websocket: Any,
    ) -> None:
        """Procesa los mensajes recibidos desde la extensión."""
        try:
            data = json.loads(raw_message)

            if not isinstance(data, dict):
                raise ValueError("El mensaje debe ser un objeto JSON.")

            message_type = data.get("type")

            if message_type == MESSAGE_FINAL_RESPONSE:
                await self.handle_ai_response(data)
            else:
                logger.debug(
                    "Mensaje ignorado: tipo=%s",
                    message_type,
                )

        except (json.JSONDecodeError, ValueError) as error:
            logger.warning("Mensaje inválido: %s", error)

        except Exception:
            logger.exception("Error procesando mensaje WebSocket.")

    async def handle_ai_response(
        self,
        data: dict[str, Any],
    ) -> None:
        """Analiza una respuesta de la IA, priorizando file_operation sobre cmd."""
        text = data.get("text", "")
        sender = data.get("sender", "gemini")
        timestamp = data.get("timestamp")

        if not isinstance(text, str):
            logger.warning("Respuesta ignorada: el texto no es válido.")
            return

        if not isinstance(sender, str):
            sender = "gemini"

        if not isinstance(timestamp, str):
            timestamp = datetime.now(timezone.utc).isoformat()

        logger.info(
            "Respuesta de IA recibida: emisor=%s, caracteres=%d",
            sender,
            len(text),
        )

        # 1. Analizar si existe una operación de archivos (file_operation tiene prioridad)
        file_op_result: FileOperationParseResult = self.agent.process_file_operation(text)

        if file_op_result.error:
            await self.broadcast({
                "type": MESSAGE_FILE_OPERATION_RESULT,
                "status": "parse_error",
                "message": file_op_result.error,
            })
            return

        if file_op_result.found and file_op_result.operation:
            plain_text = extract_plain_text(text)
            if plain_text:
                print(f"\n[IA ({sender})]: {plain_text}")

            op = file_op_result.operation
            action = op.get("action", "")

            # Si la acción es mutadora, requiere aprobación por CLI
            if is_mutating_action(action):
                pending_op = PendingFileOperation(
                    operation=op,
                    sender=sender,
                    timestamp=timestamp,
                )
                await self.pending_file_ops.put(pending_op)
                logger.info(
                    "Operación mutadora de archivo '%s' en espera de autorización local.",
                    action
                )
            else:
                # Si es de lectura, se ejecuta inmediatamente
                await self.execute_and_publish_file_operation(op)
            return

        # 2. Si no es file_operation, evaluar si existe un comando de terminal (cmd)
        cmd_result: CommandParseResult = self.agent.process_message(text)

        if cmd_result.error:
            await self.broadcast({
                "type": MESSAGE_COMMAND_RESULT,
                "status": "parse_error",
                "message": cmd_result.error,
            })
            return

        if not cmd_result.found or not cmd_result.command:
            plain_text = extract_plain_text(text)
            if plain_text:
                print(f"\n[IA ({sender})]: {plain_text}\n")
            return

        plain_text = extract_plain_text(text)
        if plain_text:
            print(f"\n[IA ({sender})]: {plain_text}")

        pending = PendingCommand(
            command=cmd_result.command,
            sender=sender,
            timestamp=timestamp,
        )

        await self.pending_commands.put(pending)

        await self.broadcast({
            "type": MESSAGE_COMMAND_PROPOSAL,
            "command": pending.command,
            "sender": pending.sender,
            "timestamp": pending.timestamp,
            "status": "pending_approval",
        })

        logger.info(
            "Comando en espera de autorización local; no se ejecutará "
            "hasta recibir aprobación explícita."
        )

    async def execute_and_publish_file_operation(
        self,
        operation: dict[str, Any]
    ) -> None:
        """Ejecuta una operación de archivo con FileTools y transmite el resultado."""
        try:
            res = self.file_tools.execute(operation)
            formatted = format_result(res)
            
            # Mostrar resultado de forma legible en la CLI local
            print(f"\n[RESULTADO ARCHIVO ({operation.get('action', 'unknown')}):]")
            print(formatted)
            print()

            await self.broadcast({
                "type": MESSAGE_FILE_OPERATION_RESULT,
                "status": "success",
                "operation": operation,
                "output": formatted,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            logger.info("Operación de archivo '%s' ejecutada exitosamente.", operation.get("action"))
        except FileToolError as err:
            error_payload = {"ok": False, "error": str(err)}
            formatted_error = format_result(error_payload)
            
            print(f"\n[ERROR ARCHIVO ({operation.get('action', 'unknown')}):]")
            print(formatted_error)
            print()

            await self.broadcast({
                "type": MESSAGE_FILE_OPERATION_RESULT,
                "status": "error",
                "operation": operation,
                "output": formatted_error,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            logger.warning("Error en operación de archivo '%s': %s", operation.get("action"), err)
        except Exception:
            logger.exception("Error inesperado ejecutando operación de archivo.")
            error_payload = {"ok": False, "error": "Error interno del servidor al ejecutar operación de archivo."}
            formatted_error = format_result(error_payload)
            
            print(f"\n[ERROR INTERNO ARCHIVO]:\n{formatted_error}\n")

            await self.broadcast({
                "type": MESSAGE_FILE_OPERATION_RESULT,
                "status": "error",
                "operation": operation,
                "output": formatted_error,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

    async def publish_command_result(
        self,
        command: str,
        status: str,
        output: str,
        return_code: int | None = None,
    ) -> None:
        """Distribuye el resultado de un comando a los clientes."""
        await self.broadcast({
            "type": MESSAGE_COMMAND_RESULT,
            "command": command,
            "status": status,
            "output": output[-MAX_OUTPUT_CHARS:],
            "return_code": return_code,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    async def publish_cli_prompt(self, text: str) -> None:
        """Envía texto libre escrito desde la CLI a los clientes."""
        await self.broadcast({
            "type": MESSAGE_CLI_PROMPT,
            "text": text,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    async def start(self) -> None:
        logger.info(
            "Servidor WebSocket escuchando en ws://%s:%s",
            HOST,
            PORT,
        )

        async with websockets.serve(
            self.handle_client,
            HOST,
            PORT,
            max_size=1_000_000,
        ):
            await self.broadcast({
                "type": MESSAGE_SERVER_STATUS,
                "connected": True,
                "message": "Servidor del agente iniciado.",
            })

            await asyncio.Future()


# ============================================================
# EJECUCIÓN AUTORIZADA
# ============================================================

async def execute_authorized_command(command: str) -> tuple[int, str]:
    """Ejecuta un comando después de la aprobación explícita en la CLI."""
    process = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    try:
        output_bytes, _ = await asyncio.wait_for(
            process.communicate(),
            timeout=COMMAND_TIMEOUT_SECONDS,
        )

        output = output_bytes.decode("utf-8", errors="replace")

        if len(output) > MAX_OUTPUT_CHARS:
            output = (
                output[-MAX_OUTPUT_CHARS:]
                + "\n[Salida truncada por límite de tamaño.]"
            )

        return process.returncode or 0, output

    except asyncio.TimeoutError:
        process.kill()
        output_bytes, _ = await process.communicate()

        output = output_bytes.decode("utf-8", errors="replace")

        return (
            -1,
            output + "\n[Comando cancelado: tiempo límite excedido.]",
        )


# ============================================================
# CLI
# ============================================================

async def request_cli_input(prompt: str) -> str:
    """Lee la entrada de la terminal sin bloquear el event loop."""
    return await aioconsole.ainput(prompt)


async def process_pending_command(
    server: WebSocketServer,
    pending: PendingCommand,
) -> None:
    """Solicita aprobación, rechazo o una alternativa libre para un comando cmd."""
    print("\n" + "=" * 72)
    print("PROPUESTA DE COMANDO")
    print("=" * 72)
    print(pending.command)
    print("=" * 72)
    print("y = autorizar y ejecutar")
    print("n = rechazar")
    print("a = escribir una alternativa libre")
    print()

    choice = (
        await request_cli_input("Decisión [y/n/a]: ")
    ).strip().lower()

    if choice == "y":
        print("\nEjecutando comando autorizado...\n")

        try:
            return_code, output = await execute_authorized_command(
                pending.command
            )

            status = "success" if return_code == 0 else "error"

            print(output or "[El comando no produjo salida.]")
            print(f"\nCódigo de salida: {return_code}")

            await server.publish_command_result(
                command=pending.command,
                status=status,
                output=output,
                return_code=return_code,
            )

        except Exception:
            logger.exception("Error ejecutando el comando autorizado.")

            await server.publish_command_result(
                command=pending.command,
                status="error",
                output="No se pudo completar la ejecución.",
            )

    elif choice == "a":
        alternative = await request_cli_input(
            "Escribe libremente tu alternativa: "
        )

        if alternative.strip():
            await server.publish_cli_prompt(alternative)
            print("Alternativa enviada a los clientes conectados.")
        else:
            print("Alternativa vacía; no se envió nada.")

        await server.broadcast({
            "type": MESSAGE_COMMAND_RESULT,
            "command": pending.command,
            "status": "rejected",
            "output": "El usuario eligió una alternativa libre.",
        })

    else:
        await server.publish_command_result(
            command=pending.command,
            status="rejected",
            output="Comando rechazado por el usuario.",
        )

        print("Comando rechazado. No se ejecutó.")

    print()


async def process_pending_file_operation(
    server: WebSocketServer,
    pending: PendingFileOperation,
) -> None:
    """Solicita aprobación o rechazo para una operación mutadora de archivos."""
    print("\n" + "=" * 72)
    print("PROPUESTA DE MODIFICACIÓN DE ARCHIVO")
    print("=" * 72)
    print(json.dumps(pending.operation, ensure_ascii=False, indent=2))
    print("=" * 72)
    print("y = autorizar y aplicar cambios")
    print("n = rechazar")
    print()

    choice = (
        await request_cli_input("Decisión [y/n]: ")
    ).strip().lower()

    if choice == "y":
        print("\nAplicando modificación autorizada...\n")
        await server.execute_and_publish_file_operation(pending.operation)
    else:
        rejection_payload = {"ok": False, "error": "Operación de archivo rechazada por el usuario."}
        formatted_rejection = format_result(rejection_payload)
        
        print("\n[OPERACIÓN DE ARCHIVO RECHAZADA]")
        print(formatted_rejection)
        print()

        await server.broadcast({
            "type": MESSAGE_FILE_OPERATION_RESULT,
            "status": "rejected",
            "operation": pending.operation,
            "output": formatted_rejection,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        print("Operación de archivo rechazada. No se modificó el sistema.")

    print()


async def cli_loop(server: WebSocketServer) -> None:
    """Permite escribir prompts libremente y atender propuestas pendientes."""
    print("\nCLI del agente")
    print("Escribe texto para enviarlo a los clientes.")
    print("Escribe /salir para cerrar el servidor.\n")

    while not server.stopping:
        input_task = asyncio.create_task(
            request_cli_input("agent> ")
        )
        pending_cmd_task = asyncio.create_task(
            server.pending_commands.get()
        )
        pending_file_task = asyncio.create_task(
            server.pending_file_ops.get()
        )

        done, waiting = await asyncio.wait(
            {input_task, pending_cmd_task, pending_file_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        if pending_cmd_task in done:
            pending_cmd = pending_cmd_task.result()

            for task in (input_task, pending_file_task):
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

            await process_pending_command(server, pending_cmd)
            continue

        if pending_file_task in done:
            pending_file = pending_file_task.result()

            for task in (input_task, pending_cmd_task):
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

            await process_pending_file_operation(server, pending_file)
            continue

        # Cancelar tareas pendientes no completadas
        for task in (pending_cmd_task, pending_file_task):
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        try:
            text = input_task.result()
        except (EOFError, KeyboardInterrupt):
            server.stopping = True
            break

        if text.strip() == "/salir":
            server.stopping = True
            break

        if text.strip():
            await server.publish_cli_prompt(text)
            print("Mensaje enviado a los clientes conectados.")

    logger.info("CLI detenida.")


# ============================================================
# PUNTO DE ENTRADA
# ============================================================

async def main() -> None:
    configure_logging()

    root_path = Path(".").resolve()
    file_tools = FileTools(root=root_path)
    agent = Agent()

    server = WebSocketServer(agent=agent, file_tools=file_tools)

    server_task = asyncio.create_task(server.start())

    try:
        await cli_loop(server)
    finally:
        server.stopping = True
        server_task.cancel()

        await asyncio.gather(
            server_task,
            return_exceptions=True,
        )

        logger.info("Servidor detenido.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nServidor detenido por el usuario.")
