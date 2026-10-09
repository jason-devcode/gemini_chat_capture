"""Servidor WebSocket para la comunicación entre la extensión y el agente."""

import asyncio
import json
import logging
from datetime import datetime
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

from agent import Agent, AgentResult


# ============================================================
# CONFIGURACIÓN
# ============================================================

HOST = "0.0.0.0"
PORT = 8765

LOG_LEVEL = logging.INFO

MESSAGE_TYPE_FINAL_RESPONSE = "GEMINI_FINAL_RESPONSE"


# ============================================================
# LOGGING
# ============================================================

def configure_logging() -> None:
    """Configura el sistema centralizado de logs."""
    logging.basicConfig(
        level=LOG_LEVEL,
        format=(
            "%(asctime)s | %(levelname)-8s | "
            "%(name)s | %(message)s"
        ),
        datefmt="%Y-%m-%d %H:%M:%S",
    )


logger = logging.getLogger("server")


# ============================================================
# VALIDACIÓN Y NORMALIZACIÓN DE MENSAJES
# ============================================================

class MessageValidationError(ValueError):
    """Indica que un mensaje recibido no tiene una estructura válida."""


def parse_message(raw_message: str) -> dict[str, Any]:
    """
    Convierte un mensaje JSON en un diccionario y valida
    su estructura básica.
    """
    try:
        data = json.loads(raw_message)
    except json.JSONDecodeError as error:
        raise MessageValidationError(
            f"JSON inválido en la posición {error.pos}."
        ) from error

    if not isinstance(data, dict):
        raise MessageValidationError(
            "El mensaje JSON debe ser un objeto."
        )

    message_type = data.get("type")

    if not isinstance(message_type, str) or not message_type:
        raise MessageValidationError(
            "El campo 'type' es obligatorio y debe ser texto."
        )

    return data


def parse_timestamp(value: Any) -> datetime:
    """Interpreta una fecha ISO 8601 o utiliza la hora local actual."""
    if value is None or value == "":
        return datetime.now().astimezone()

    if not isinstance(value, str):
        raise MessageValidationError(
            "El campo 'timestamp' debe ser una cadena ISO 8601."
        )

    try:
        # Compatibilidad con fechas ISO que terminan en Z.
        normalized_value = (
            value[:-1] + "+00:00"
            if value.endswith("Z")
            else value
        )

        return datetime.fromisoformat(normalized_value)

    except ValueError as error:
        raise MessageValidationError(
            "El campo 'timestamp' no contiene una fecha ISO 8601 válida."
        ) from error


def extract_final_response(
    data: dict[str, Any],
) -> tuple[str, str, datetime]:
    """Extrae y valida los campos necesarios para el agente."""
    sender = data.get("sender", "gemini")
    text = data.get("text", "")

    if not isinstance(sender, str) or not sender.strip():
        raise MessageValidationError(
            "El campo 'sender' debe ser una cadena no vacía."
        )

    if not isinstance(text, str):
        raise MessageValidationError(
            "El campo 'text' debe ser una cadena."
        )

    timestamp = parse_timestamp(data.get("timestamp"))

    return sender.strip(), text, timestamp


# ============================================================
# PRESENTACIÓN DE MENSAJES
# ============================================================

def log_final_response(
    sender: str,
    text: str,
    timestamp: datetime,
) -> None:
    """Registra la respuesta de la IA recibida."""
    readable_timestamp = timestamp.strftime(
        "%A, %d de %B de %Y a las %I:%M %p"
    )

    logger.info(
        "Respuesta recibida | fecha=%s | emisor=%s | caracteres=%d",
        readable_timestamp,
        sender.upper(),
        len(text),
    )

    # El contenido puede contener información sensible.
    # Se evita imprimirlo completo por defecto.


def log_agent_result(result: AgentResult) -> None:
    """Registra el resultado del procesamiento del agente."""
    logger.info(
        "Agente | status=%s | message=%s",
        result.status,
        result.message,
    )


# ============================================================
# PROCESAMIENTO DE MENSAJES
# ============================================================

class MessageHandler:
    """Coordina la validación y el procesamiento de mensajes."""

    def __init__(self, agent: Agent) -> None:
        self.agent = agent

    async def handle(self, raw_message: str) -> None:
        """Procesa un mensaje entrante sin acoplarse a la red."""
        try:
            data = parse_message(raw_message)
        except MessageValidationError as error:
            logger.warning("Mensaje rechazado: %s", error)
            return

        message_type = data["type"]

        if message_type != MESSAGE_TYPE_FINAL_RESPONSE:
            logger.debug(
                "Tipo de mensaje ignorado: %s",
                message_type,
            )
            return

        try:
            sender, text, timestamp = extract_final_response(data)
        except MessageValidationError as error:
            logger.warning(
                "Respuesta final inválida: %s",
                error,
            )
            return

        log_final_response(sender, text, timestamp)

        try:
            result = await self.agent.process_message(
                text=text,
                context={
                    "sender": sender,
                    "timestamp": timestamp.isoformat(),
                },
            )
            log_agent_result(result)

        except Exception:
            # La excepción se registra con su traceback.
            # El fallo del agente no debe derribar el servidor.
            logger.exception(
                "Error inesperado procesando la respuesta con el agente."
            )


# ============================================================
# SERVIDOR WEBSOCKET
# ============================================================

class WebSocketServer:
    """Gestiona las conexiones de clientes WebSocket."""

    def __init__(
        self,
        host: str,
        port: int,
        message_handler: MessageHandler,
    ) -> None:
        self.host = host
        self.port = port
        self.message_handler = message_handler

    async def handle_client(self, websocket: Any) -> None:
        """Gestiona el ciclo de vida de una conexión."""
        client_address = websocket.remote_address

        logger.info("Cliente conectado: %s", client_address)

        try:
            async for raw_message in websocket:
                await self.handle_message(raw_message, client_address)

        except ConnectionClosed as error:
            logger.info(
                "Conexión cerrada | cliente=%s | código=%s",
                client_address,
                error.code,
            )

        except Exception:
            logger.exception(
                "Error inesperado en la conexión del cliente %s.",
                client_address,
            )

        finally:
            logger.info("Cliente desconectado: %s", client_address)

    async def handle_message(
        self,
        raw_message: str,
        client_address: Any,
    ) -> None:
        """Aísla los errores de procesamiento de cada mensaje."""
        try:
            await self.message_handler.handle(raw_message)

        except Exception:
            logger.exception(
                "Error procesando un mensaje de %s.",
                client_address,
            )

    async def start(self) -> None:
        """Inicia el servidor y mantiene activo el servicio."""
        logger.info(
            "Iniciando servidor WebSocket en ws://%s:%s",
            self.host,
            self.port,
        )

        async with websockets.serve(
            self.handle_client,
            self.host,
            self.port,
        ):
            logger.info("Servidor WebSocket iniciado correctamente.")
            await asyncio.Future()


# ============================================================
# PUNTO DE ENTRADA
# ============================================================

async def main() -> None:
    """Construye las dependencias e inicia la aplicación."""
    configure_logging()

    agent = Agent()
    message_handler = MessageHandler(agent)

    server = WebSocketServer(
        host=HOST,
        port=PORT,
        message_handler=message_handler,
    )

    await server.start()


if __name__ == "__main__":
    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        logging.getLogger("server").info(
            "Servidor detenido por el usuario."
        )

    except Exception:
        logging.getLogger("server").exception(
            "Error fatal en el servidor."
        )
        raise
