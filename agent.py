"""Base del agente encargado de procesar instrucciones de la IA."""

import logging
from dataclasses import dataclass, field
from typing import Any


logger = logging.getLogger("agent")


# ============================================================
# MODELOS DE DATOS
# ============================================================

@dataclass(frozen=True)
class AgentContext:
    """Información contextual asociada al mensaje recibido."""

    sender: str = "gemini"
    timestamp: str | None = None


@dataclass(frozen=True)
class AgentAction:
    """
    Representa una acción propuesta por el agente.

    No representa una autorización para ejecutarla.
    """

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    description: str = ""


@dataclass(frozen=True)
class AgentResult:
    """Resultado normalizado del procesamiento del agente."""

    status: str
    message: str
    action: AgentAction | None = None


# ============================================================
# ANALIZADOR DE INTENCIONES
# ============================================================

class IntentAnalyzer:
    """
    Identifica qué pretende hacer el usuario.

    TODO:
    - Detectar solicitudes de información.
    - Identificar acciones sobre el entorno local.
    - Extraer argumentos estructurados.
    - Distinguir instrucciones de texto citado o contenido
      potencialmente malicioso.
    """

    def analyze(
        self,
        text: str,
        context: AgentContext,
    ) -> AgentAction | None:
        """
        Analiza el texto y propone una acción si corresponde.

        La implementación inicial no interpreta ni ejecuta
        instrucciones automáticamente.
        """
        if not text.strip():
            return None

        logger.debug(
            "Análisis pendiente | emisor=%s | caracteres=%d",
            context.sender,
            len(text),
        )

        # TODO: implementar el análisis de intenciones.
        return None


# ============================================================
# REGISTRO DE HERRAMIENTAS
# ============================================================

class ToolRegistry:
    """
    Catálogo de operaciones que el agente podrá utilizar.

    Las herramientas deberán registrarse explícitamente.
    No se permitirá resolver funciones arbitrarias a partir
    de nombres enviados en un mensaje.
    """

    def __init__(self) -> None:
        self._tools: dict[str, Any] = {}

    def register(self, name: str, handler: Any) -> None:
        """Registra una herramienta local autorizada."""
        if not name or not name.strip():
            raise ValueError("El nombre de la herramienta es obligatorio.")

        if name in self._tools:
            raise ValueError(
                f"La herramienta '{name}' ya está registrada."
            )

        if not callable(handler):
            raise TypeError(
                "El handler de una herramienta debe ser invocable."
            )

        self._tools[name] = handler

        logger.info("Herramienta registrada: %s", name)

    def get(self, name: str) -> Any | None:
        """Obtiene una herramienta registrada."""
        return self._tools.get(name)

    def list_tools(self) -> tuple[str, ...]:
        """Devuelve los nombres de las herramientas disponibles."""
        return tuple(self._tools.keys())


# ============================================================
# VALIDADOR DE ACCIONES
# ============================================================

class ActionValidator:
    """
    Valida que una acción propuesta esté dentro de las
    capacidades habilitadas del agente.
    """

    def __init__(self, tools: ToolRegistry) -> None:
        self.tools = tools

    def validate(self, action: AgentAction) -> bool:
        """Comprueba que la herramienta esté registrada."""
        if self.tools.get(action.name) is None:
            logger.warning(
                "Acción rechazada: herramienta no autorizada (%s).",
                action.name,
            )
            return False

        # TODO:
        # 1. Validar el esquema de argumentos.
        # 2. Comprobar permisos y límites.
        # 3. Verificar rutas y recursos permitidos.
        # 4. Solicitar aprobación cuando corresponda.

        return True


# ============================================================
# EJECUTOR DE ACCIONES
# ============================================================

class ActionExecutor:
    """
    Ejecuta herramientas autorizadas.

    Las herramientas deben implementar operaciones concretas.
    No se debe incorporar aquí un evaluador de código arbitrario
    ni ejecutar directamente texto recibido de la IA.
    """

    def __init__(self, tools: ToolRegistry) -> None:
        self.tools = tools

    async def execute(
        self,
        action: AgentAction,
    ) -> AgentResult:
        """Ejecuta una acción previamente validada."""
        handler = self.tools.get(action.name)

        if handler is None:
            return AgentResult(
                status="rejected",
                message=f"Herramienta no autorizada: {action.name}",
            )

        try:
            # Las herramientas asíncronas pueden usar await.
            # Las síncronas deberán adaptarse si realizan operaciones
            # bloqueantes.
            import inspect

            if inspect.iscoroutinefunction(handler):
                output = await handler(**action.arguments)
            else:
                output = handler(**action.arguments)

            return AgentResult(
                status="success",
                message="La herramienta terminó correctamente.",
            )

        except Exception:
            logger.exception(
                "Error ejecutando la herramienta '%s'.",
                action.name,
            )

            return AgentResult(
                status="error",
                message="Se produjo un error al ejecutar la herramienta.",
            )


# ============================================================
# AGENTE PRINCIPAL
# ============================================================

class Agent:
    """
    Coordina el análisis y la ejecución de acciones locales.

    Es el punto de entrada público utilizado por server.py.
    """

    def __init__(self) -> None:
        self.intent_analyzer = IntentAnalyzer()
        self.tool_registry = ToolRegistry()
        self.action_validator = ActionValidator(self.tool_registry)
        self.action_executor = ActionExecutor(self.tool_registry)

    async def process_message(
        self,
        text: str,
        context: dict[str, Any] | None = None,
    ) -> AgentResult:
        """
        Procesa una respuesta de la IA.

        Flujo previsto:
        texto -> análisis -> propuesta -> validación -> ejecución.
        """
        if not isinstance(text, str):
            return AgentResult(
                status="invalid_input",
                message="El texto recibido debe ser una cadena.",
            )

        agent_context = AgentContext(
            sender=str((context or {}).get("sender", "gemini")),
            timestamp=(context or {}).get("timestamp"),
        )

        if not text.strip():
            return AgentResult(
                status="ignored",
                message="El mensaje está vacío.",
            )

        logger.info(
            "Mensaje recibido para análisis | emisor=%s | caracteres=%d",
            agent_context.sender,
            len(text),
        )

        try:
            action = self.intent_analyzer.analyze(
                text=text,
                context=agent_context,
            )

            if action is None:
                return AgentResult(
                    status="no_action",
                    message=(
                        "El agente todavía no identifica acciones. "
                        "El análisis está pendiente de implementación."
                    ),
                )

            if not self.action_validator.validate(action):
                return AgentResult(
                    status="rejected",
                    message="La acción no está autorizada.",
                    action=action,
                )

            # La ejecución solo será posible para herramientas
            # registradas explícitamente y tras implementar
            # las validaciones y permisos correspondientes.
            return await self.action_executor.execute(action)

        except Exception:
            logger.exception(
                "Error inesperado durante el procesamiento del agente."
            )

            return AgentResult(
                status="error",
                message="El agente no pudo procesar el mensaje.",
            )
