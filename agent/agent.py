"""Extracción segura de propuestas de comandos desde texto de IA."""

import logging
import re
from dataclasses import dataclass


logger = logging.getLogger("agent.parser")

BLOCK_PATTERN = re.compile(r"\b(code|cmd)\s*\{", re.IGNORECASE)


# ============================================================
# MODELOS
# ============================================================

@dataclass(frozen=True)
class CommandParseResult:
    """Resultado del análisis de un mensaje."""

    found: bool
    command: str | None = None
    error: str | None = None


# ============================================================
# PARSER
# ============================================================

def find_matching_brace(
    text: str,
    opening_index: int,
) -> int | None:
    """
    Encuentra la llave de cierre correspondiente.

    Respeta llaves anidadas, comillas simples, dobles,
    comillas invertidas y escapes con barra invertida.

    opening_index debe apuntar a la llave de apertura.
    """
    depth = 0
    quote: str | None = None
    escaped = False

    for index in range(opening_index, len(text)):
        char = text[index]

        if escaped:
            escaped = False
            continue

        if char == "\\":
            escaped = True
            continue

        if quote is not None:
            if char == quote:
                quote = None
            continue

        if char in ("'", '"', "`"):
            quote = char
            continue

        if char == "{":
            depth += 1

        elif char == "}":
            depth -= 1

            if depth == 0:
                return index

    return None


def parse_first_command(text: str) -> CommandParseResult:
    """
    Extrae como máximo el primer bloque cmd fuera de bloques code.

    Esta función solamente analiza el texto. No ejecuta comandos
    ni determina si son seguros.
    """
    if not isinstance(text, str) or not text:
        return CommandParseResult(found=False)

    position = 0

    while True:
        match = BLOCK_PATTERN.search(text, position)

        if match is None:
            return CommandParseResult(found=False)

        block_type = match.group(1).lower()
        opening_index = match.end() - 1

        closing_index = find_matching_brace(
            text,
            opening_index,
        )

        if closing_index is None:
            if block_type == "cmd":
                logger.warning(
                    "Se encontró un bloque cmd sin cerrar."
                )

                return CommandParseResult(
                    found=True,
                    error="El bloque cmd no tiene una llave de cierre.",
                )

            # Un bloque code sin cerrar invalida el resto del mensaje
            # para la extracción de comandos.
            logger.warning(
                "Se encontró un bloque code sin cerrar."
            )

            return CommandParseResult(found=False)

        if block_type == "code":
            # Saltar todo el contenido del bloque de código.
            position = closing_index + 1
            continue

        command = text[opening_index + 1:closing_index].strip()

        if not command:
            logger.warning("Se encontró un bloque cmd vacío.")

            return CommandParseResult(
                found=True,
                error="El bloque cmd está vacío.",
            )

        logger.info(
            "Propuesta de comando detectada | caracteres=%d",
            len(command),
        )

        return CommandParseResult(
            found=True,
            command=command,
        )


# ============================================================
# AGENTE
# ============================================================

class Agent:
    """Analiza mensajes de IA y extrae propuestas de comandos."""

    def process_message(self, text: str) -> CommandParseResult:
        """
        Analiza un mensaje y devuelve la primera propuesta válida.

        No ejecuta comandos. La autorización corresponde al usuario
        y debe gestionarse en el servidor.
        """
        result = parse_first_command(text)

        if result.error:
            logger.warning(
                "No se pudo interpretar la propuesta: %s",
                result.error,
            )

        elif result.found:
            logger.info(
                "Propuesta detectada; requiere autorización humana."
            )

        else:
            logger.debug(
                "No se detectaron propuestas de comandos."
            )

        return result
