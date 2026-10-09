"""Servidor WebSocket con CLI, aprobación local y ejecución controlada."""

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import aioconsole
import websockets
from websockets.exceptions import ConnectionClosed

from agent import Agent, CommandParseResult


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


# ============================================================
# SERVIDOR WEBSOCKET
# ============================================================

class WebSocketServer:
    """Administra conexiones y distribuye mensajes a los clientes."""

    def __init__(self, agent: Agent) -> None:
        self.agent = agent
        self.clients: set[Any] = set()
        self.pending_commands: asyncio.Queue[PendingCommand] = asyncio.Queue()
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
        """Analiza una respuesta de la IA y registra su propuesta."""
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

        result: CommandParseResult = self.agent.process_message(text)

        if result.error:
            await self.broadcast({
                "type": MESSAGE_COMMAND_RESULT,
                "status": "parse_error",
                "message": result.error,
            })
            return

        if not result.found or not result.command:
            return

        pending = PendingCommand(
            command=result.command,
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
    """
    Ejecuta un comando después de la aprobación explícita en la CLI.

    Se utiliza una shell para respetar el formato de comandos
    propuesto. Por eso esta función solo debe invocarse después
    de una aprobación humana consciente.
    """
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
    """Solicita aprobación, rechazo o una alternativa libre."""
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
            # El texto se distribuye como una instrucción libre,
            # nunca como una autorización para ejecutar un comando.
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


async def cli_loop(server: WebSocketServer) -> None:
    """
    Permite escribir prompts libremente y atender propuestas pendientes.

    Durante la espera de entrada, la llegada de un comando pendiente
    interrumpe la espera para solicitar autorización.
    """
    print("\nCLI del agente")
    print("Escribe texto para enviarlo a los clientes.")
    print("Escribe /salir para cerrar el servidor.\n")

    while not server.stopping:
        input_task = asyncio.create_task(
            request_cli_input("agent> ")
        )
        pending_task = asyncio.create_task(
            server.pending_commands.get()
        )

        done, waiting = await asyncio.wait(
            {input_task, pending_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        if pending_task in done:
            pending = pending_task.result()

            if not input_task.done():
                input_task.cancel()
                await asyncio.gather(
                    input_task,
                    return_exceptions=True,
                )

            await process_pending_command(server, pending)
            continue

        pending_task.cancel()
        await asyncio.gather(
            pending_task,
            return_exceptions=True,
        )

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

    server = WebSocketServer(agent=Agent())

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
