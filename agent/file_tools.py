"""Operaciones de archivos acotadas a una raíz autorizada.

No ejecuta comandos de shell. Las rutas recibidas deben ser relativas a root.
Las mutaciones deben pasar por una capa de autorización antes de invocar execute().
"""
from __future__ import annotations

import difflib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any


READ_ONLY_ACTIONS = {
    "list_dir", "read_file", "get_lines", "find_text", "search_code",
    "locate_text", "diff_files",
}
MUTATING_ACTIONS = {
    "create_file", "write_file", "append_file", "delete_file",
    "insert_lines", "replace_lines", "replace_columns", "replace_text",
}
ALL_ACTIONS = READ_ONLY_ACTIONS | MUTATING_ACTIONS


class FileToolError(Exception):
    """Error controlado de validación u operación de archivos."""


class FileTools:
    def __init__(self, root: str | Path, *, max_read_bytes: int = 2_000_000):
        self.root = Path(root).expanduser().resolve()
        self.max_read_bytes = max_read_bytes
        if not self.root.exists() or not self.root.is_dir():
            raise FileToolError(f"La raíz configurada no es un directorio: {self.root}")

    def _path(self, value: str, *, allow_root: bool = False) -> Path:
        if not isinstance(value, str) or not value.strip():
            raise FileToolError("'path' debe ser una ruta relativa no vacía.")
        candidate = Path(value).expanduser()
        if candidate.is_absolute():
            raise FileToolError("Usa rutas relativas a la raíz autorizada.")
        resolved = (self.root / candidate).resolve(strict=False)
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise FileToolError("La ruta sale de la raíz autorizada.") from exc
        if resolved == self.root and not allow_root:
            raise FileToolError("Esta operación requiere un archivo dentro de la raíz.")
        return resolved

    @staticmethod
    def _line_number(value: Any, name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise FileToolError(f"'{name}' debe ser un entero >= 1 (líneas 1-based).")
        return value

    def _read_text(self, path: Path) -> str:
        if not path.exists() or not path.is_file():
            raise FileToolError(f"No existe el archivo: {path.relative_to(self.root)}")
        if path.stat().st_size > self.max_read_bytes:
            raise FileToolError(
                f"El archivo supera el límite de lectura de {self.max_read_bytes} bytes. "
                "Solicita un rango de líneas o aumenta el límite explícitamente."
            )
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise FileToolError("El archivo no parece ser texto UTF-8.") from exc

    def _write_atomic(self, path: Path, content: str, *, overwrite: bool) -> dict[str, Any]:
        path.parent.mkdir(parents=False, exist_ok=True)
        if path.exists() and not overwrite:
            raise FileToolError("El archivo ya existe; usa write_file con overwrite=true para reemplazarlo.")
        if path.exists() and not path.is_file():
            raise FileToolError("La ruta de destino no es un archivo regular.")
        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            if path.exists() and not overwrite:
                raise FileToolError("El archivo apareció durante la operación; no se sobrescribió.")
            os.replace(tmp_name, path)
        finally:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
        return {"path": path.relative_to(self.root).as_posix(), "bytes": path.stat().st_size}

    def execute(self, operation: dict[str, Any]) -> dict[str, Any]:
        """Ejecuta una operación ya validada y autorizada cuando sea mutadora."""
        if not isinstance(operation, dict):
            raise FileToolError("La operación debe ser un objeto JSON.")
        action = operation.get("action")
        if action not in ALL_ACTIONS:
            raise FileToolError(f"Acción desconocida: {action!r}. Acciones: {', '.join(sorted(ALL_ACTIONS))}")
        handler = getattr(self, f"_{action}", None)
        if handler is None:
            raise FileToolError(f"La acción '{action}' no está implementada.")
        return {"ok": True, "action": action, "result": handler(operation)}

    def _list_dir(self, op: dict[str, Any]) -> dict[str, Any]:
        path = self._path(op.get("path", "."), allow_root=True)
        if not path.is_dir():
            raise FileToolError("La ruta no es un directorio.")
        entries = []
        for item in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            entries.append({"name": item.name, "type": "directory" if item.is_dir() else "file",
                            "path": item.relative_to(self.root).as_posix()})
        return {"path": path.relative_to(self.root).as_posix() or ".", "entries": entries}

    def _read_file(self, op: dict[str, Any]) -> dict[str, Any]:
        path = self._path(op.get("path"))
        content = self._read_text(path)
        return {"path": path.relative_to(self.root).as_posix(), "content": content,
                "line_count": len(content.splitlines())}

    def _get_lines(self, op: dict[str, Any]) -> dict[str, Any]:
        path = self._path(op.get("path"))
        lines = self._read_text(path).splitlines()
        start = self._line_number(op.get("start_line"), "start_line")
        end = self._line_number(op.get("end_line", start), "end_line")
        if end < start:
            raise FileToolError("'end_line' debe ser >= 'start_line'.")
        selected = [{"line": i, "text": lines[i - 1]} for i in range(start, min(end, len(lines)) + 1)]
        return {"path": path.relative_to(self.root).as_posix(), "start_line": start,
                "end_line": min(end, len(lines)), "total_lines": len(lines), "lines": selected}

    def _find_text(self, op: dict[str, Any]) -> dict[str, Any]:
        needle = op.get("text")
        if not isinstance(needle, str) or not needle:
            raise FileToolError("'text' debe ser una cadena no vacía.")
        path = self._path(op.get("path"))
        lines = self._read_text(path).splitlines()
        case_sensitive = op.get("case_sensitive", True)
        left = needle if case_sensitive else needle.casefold()
        matches = []
        for number, line in enumerate(lines, 1):
            right = line if case_sensitive else line.casefold()
            column = right.find(left)
            if column >= 0:
                matches.append({"line": number, "column": column + 1, "text": line})
        return {"path": path.relative_to(self.root).as_posix(), "matches": matches}

    def _search_code(self, op: dict[str, Any]) -> dict[str, Any]:
        needle = op.get("text")
        if not isinstance(needle, str) or not needle:
            raise FileToolError("'text' debe ser una cadena no vacía.")
        base = self._path(op.get("path", "."), allow_root=True)
        if base.is_file():
            candidates = [base]
        elif base.is_dir():
            extensions = op.get("extensions", [".py", ".js", ".ts", ".json", ".md", ".html", ".css", ".txt"])
            if not isinstance(extensions, list) or not all(isinstance(x, str) for x in extensions):
                raise FileToolError("'extensions' debe ser una lista de extensiones.")
            candidates = [p for p in base.rglob("*") if p.is_file() and p.suffix in extensions]
        else:
            raise FileToolError("La ruta no existe.")
        max_results = op.get("max_results", 100)
        if isinstance(max_results, bool) or not isinstance(max_results, int) or not 1 <= max_results <= 1000:
            raise FileToolError("'max_results' debe estar entre 1 y 1000.")
        case_sensitive = op.get("case_sensitive", True)
        target = needle if case_sensitive else needle.casefold()
        matches = []
        for file_path in sorted(candidates):
            try:
                content = self._read_text(file_path)
            except (FileToolError, OSError):
                continue
            for number, line in enumerate(content.splitlines(), 1):
                candidate = line if case_sensitive else line.casefold()
                if target in candidate:
                    matches.append({"path": file_path.relative_to(self.root).as_posix(), "line": number, "text": line})
                    if len(matches) >= max_results:
                        return {"matches": matches, "truncated": True}
        return {"matches": matches, "truncated": False}

    def _locate_text(self, op: dict[str, Any]) -> dict[str, Any]:
        return self._search_code(op)

    def _create_file(self, op: dict[str, Any]) -> dict[str, Any]:
        path = self._path(op.get("path"))
        content = op.get("content", "")
        if not isinstance(content, str):
            raise FileToolError("'content' debe ser una cadena.")
        return self._write_atomic(path, content, overwrite=False)

    def _write_file(self, op: dict[str, Any]) -> dict[str, Any]:
        path = self._path(op.get("path"))
        content = op.get("content")
        if not isinstance(content, str):
            raise FileToolError("'content' debe ser una cadena.")
        if path.exists() and op.get("overwrite") is not True:
            raise FileToolError("Para reemplazar un archivo existente debes indicar 'overwrite': true.")
        return self._write_atomic(path, content, overwrite=op.get("overwrite") is True)

    def _append_file(self, op: dict[str, Any]) -> dict[str, Any]:
        path = self._path(op.get("path"))
        addition = op.get("content")
        if not isinstance(addition, str):
            raise FileToolError("'content' debe ser una cadena.")
        current = self._read_text(path) if path.exists() else ""
        return self._write_atomic(path, current + addition, overwrite=True)

    def _delete_file(self, op: dict[str, Any]) -> dict[str, Any]:
        path = self._path(op.get("path"))
        if not path.exists() or not path.is_file():
            raise FileToolError("El archivo no existe o no es un archivo regular.")
        path.unlink()
        return {"path": path.relative_to(self.root).as_posix(), "deleted": True}

    def _insert_lines(self, op: dict[str, Any]) -> dict[str, Any]:
        path = self._path(op.get("path"))
        lines = self._read_text(path).splitlines(keepends=True)
        after = op.get("after_line")
        if isinstance(after, bool) or not isinstance(after, int) or after < 0 or after > len(lines):
            raise FileToolError("'after_line' debe estar entre 0 y el total de líneas; 0 inserta al inicio.")
        new_lines = op.get("lines")
        if isinstance(new_lines, str):
            new_lines = new_lines.splitlines(keepends=True)
        if not isinstance(new_lines, list) or not all(isinstance(x, str) for x in new_lines):
            raise FileToolError("'lines' debe ser una lista de cadenas o una cadena multilínea.")
        normalized = [line if line.endswith("\n") else line + "\n" for line in new_lines]
        lines[after:after] = normalized
        return self._write_atomic(path, "".join(lines), overwrite=True) | {"inserted_lines": len(normalized)}

    def _replace_lines(self, op: dict[str, Any]) -> dict[str, Any]:
        path = self._path(op.get("path"))
        original = self._read_text(path)
        lines = original.splitlines(keepends=True)
        start = self._line_number(op.get("start_line"), "start_line")
        end = self._line_number(op.get("end_line", start), "end_line")
        if end < start or end > len(lines):
            raise FileToolError("El rango de líneas no es válido para el archivo.")
        replacement = op.get("replacement")
        if isinstance(replacement, str):
            replacement = replacement.splitlines(keepends=True)
        if not isinstance(replacement, list) or not all(isinstance(x, str) for x in replacement):
            raise FileToolError("'replacement' debe ser una lista de cadenas o una cadena multilínea.")
        replacement = [line if line.endswith("\n") else line + "\n" for line in replacement]
        lines[start - 1:end] = replacement
        result = self._write_atomic(path, "".join(lines), overwrite=True)
        return result | {"replaced_range": [start, end], "replacement_lines": len(replacement)}

    def _replace_columns(self, op: dict[str, Any]) -> dict[str, Any]:
        path = self._path(op.get("path"))
        lines = self._read_text(path).splitlines(keepends=True)
        line_number = self._line_number(op.get("line"), "line")
        start = self._line_number(op.get("start_column"), "start_column")
        end = self._line_number(op.get("end_column", start), "end_column")
        replacement = op.get("replacement", "")
        if not isinstance(replacement, str):
            raise FileToolError("'replacement' debe ser una cadena.")
        if line_number > len(lines) or end < start:
            raise FileToolError("Línea o rango de columnas no válido.")
        body = lines[line_number - 1]
        newline = "\n" if body.endswith("\n") else ""
        body = body[:-1] if newline else body
        # Columnas 1-based; end_column es inclusiva.
        body = body[:start - 1] + replacement + body[end:]
        lines[line_number - 1] = body + newline
        return self._write_atomic(path, "".join(lines), overwrite=True) | {"line": line_number}

    def _replace_text(self, op: dict[str, Any]) -> dict[str, Any]:
        path = self._path(op.get("path"))
        old = op.get("old_text")
        new = op.get("new_text")
        if not isinstance(old, str) or not old:
            raise FileToolError("'old_text' debe ser una cadena no vacía.")
        if not isinstance(new, str):
            raise FileToolError("'new_text' debe ser una cadena.")
        content = self._read_text(path)
        count = content.count(old)
        expected = op.get("expected_count", 1)
        if count != expected:
            raise FileToolError(f"Se esperaban {expected} coincidencias exactas, pero se encontraron {count}; no se modificó el archivo.")
        result = self._write_atomic(path, content.replace(old, new), overwrite=True)
        return result | {"replacements": count}

    def _diff_files(self, op: dict[str, Any]) -> dict[str, Any]:
        left = self._path(op.get("path"))
        right = self._path(op.get("other_path"))
        diff = difflib.unified_diff(self._read_text(left).splitlines(), self._read_text(right).splitlines(),
                                    fromfile=left.relative_to(self.root).as_posix(),
                                    tofile=right.relative_to(self.root).as_posix(), lineterm="")
        return {"diff": "\n".join(diff)}


def is_mutating_action(action: str) -> bool:
    return action in MUTATING_ACTIONS


def format_result(result: dict[str, Any]) -> str:
    """Serializa resultados para transportarlos como datos, no como instrucciones."""
    return json.dumps(result, ensure_ascii=False, indent=2)
