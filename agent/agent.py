"""Parser de bloques file_operation, cmd y code.

IMPORTANTE: invoca estos parsers únicamente sobre texto generado por el modelo.
Nunca vuelvas a parsear como instrucciones el resultado devuelto por file_tools.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("agent.parser")
BLOCK_PATTERN = re.compile(r"\b(file_operation|code|cmd)\s*\{", re.IGNORECASE)

@dataclass(frozen=True)
class CommandParseResult:
    found: bool
    command: str | None = None
    error: str | None = None

@dataclass(frozen=True)
class FileOperationParseResult:
    found: bool
    operation: dict[str, Any] | None = None
    error: str | None = None


def find_matching_brace(text: str, opening_index: int) -> int | None:
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
        if char in ("'", '"', '`'):
            quote = char
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def _find_blocks(text: str):
    """Itera bloques de primer nivel sin buscar dentro del cuerpo ya encontrado."""
    position = 0
    while True:
        match = BLOCK_PATTERN.search(text, position)
        if match is None:
            return
        kind = match.group(1).lower()
        opening = match.end() - 1
        closing = find_matching_brace(text, opening)
        yield match, kind, opening, closing
        if closing is None:
            return
        position = closing + 1


def parse_first_file_operation(text: str) -> FileOperationParseResult:
    """Extrae la primera operación JSON; no evalúa ni ejecuta contenido."""
    if not isinstance(text, str) or not text:
        return FileOperationParseResult(False)
    for match, kind, opening, closing in _find_blocks(text):
        if kind != "file_operation":
            continue
        if closing is None:
            return FileOperationParseResult(True, error="El bloque file_operation no tiene llave de cierre.")
        raw = text[opening + 1:closing].strip()
        try:
            operation = json.loads("{" + raw + "}")
        except json.JSONDecodeError as exc:
            return FileOperationParseResult(True, error=f"JSON inválido en file_operation: {exc.msg} (columna {exc.colno}).")
        if not isinstance(operation, dict):
            return FileOperationParseResult(True, error="file_operation debe contener un objeto JSON.")
        if not isinstance(operation.get("action"), str):
            return FileOperationParseResult(True, error="La operación JSON debe incluir 'action' como cadena.")
        return FileOperationParseResult(True, operation=operation)
    return FileOperationParseResult(False)


def parse_first_command(text: str) -> CommandParseResult:
    """Extrae el primer cmd fuera de bloques code/file_operation."""
    if not isinstance(text, str) or not text:
        return CommandParseResult(False)
    for match, kind, opening, closing in _find_blocks(text):
        if kind == "file_operation":
            # La prioridad de file_operation la resuelve el servidor antes de llamar aquí.
            continue
        if kind == "code":
            if closing is None:
                return CommandParseResult(False)
            continue
        if closing is None:
            return CommandParseResult(True, error="El bloque cmd no tiene una llave de cierre.")
        command = text[opening + 1:closing].strip()
        if not command:
            return CommandParseResult(True, error="El bloque cmd está vacío.")
        return CommandParseResult(True, command=command)
    return CommandParseResult(False)


def extract_plain_text(text: str) -> str:
    """Elimina bloques de protocolo del texto original generado por el modelo."""
    if not isinstance(text, str) or not text:
        return ""
    pieces = []
    position = 0
    for match, _kind, _opening, closing in _find_blocks(text):
        pieces.append(text[position:match.start()])
        if closing is None:
            position = len(text)
            break
        position = closing + 1
    pieces.append(text[position:])
    return " ".join(part.strip() for part in pieces if part.strip()).strip()


class Agent:
    def process_file_operation(self, text: str) -> FileOperationParseResult:
        result = parse_first_file_operation(text)
        if result.error:
            logger.warning("No se pudo interpretar file_operation: %s", result.error)
        return result

    def process_message(self, text: str) -> CommandParseResult:
        return parse_first_command(text)
