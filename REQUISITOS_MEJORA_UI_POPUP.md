# Documento de Requisitos Técnicos: Interpretación de Comandos de IA en UI de Popup

## 1. Introducción y Objetivo
El objetivo de esta mejora es homogeneizar el renderizado y comportamiento de los mensajes en la interfaz de la extensión (popup.html / popup.js) con la lógica de procesamiento presente en el backend (agent/agent.py y agent/server.py).

Actualmente, el popup muestra las respuestas de la IA como bloques de texto plano continuo. Se requiere que la interfaz web del popup extraiga, formatee e interprete los bloques especiales cmd {...} y code {...} con el mismo rigor sintáctico que utiliza el agente Python.

---

## 2. Análisis del Algoritmo del Agente (agent/agent.py)

Para garantizar paridad funcional completa, la interfaz del popup debe replicar las siguientes reglas de parseo implementadas en Python:

1. Patrón de Bloques (BLOCK_PATTERN):
   - Detecta la apertura de bloques mediante la expresión regular \\b(code|cmd)\\s*\\{ (sensible/insensible a mayúsculas).

2. Balanceo de Llaves (find_matching_brace):
   - Rastrea la llave de cierre } asociada a la llave de apertura del bloque.
   - Ignores de sintaxis: Debe respetar llaves anidadas, caracteres escapados \\ y delimitadores de cadenas de texto (', ", `).

3. Estructura de Bloques:
   - code { ... }: Bloque explicativo o de código. Su contenido no debe ejecutarse. Si un bloque code no se cierra correctamente, invalida el resto del mensaje.
   - cmd { ... }: Propuesta de comando ejecutable en la máquina local.

4. Extracción Limpia (extract_plain_text):
   - Separa el texto conversacional puro de los bloques estructurados (cmd y code).

---

## 3. Requisitos Funcionales para la UI (popup.html y popup.js)

### 3.1 Parser JavaScript en popup.js
- Implementar un módulo o función de parseo idéntica a parse_first_command, find_matching_brace y extract_plain_text de agent.py.
- Reconocer y separar en cada mensaje entrante:
  - Texto conversacional (prosa previa o posterior).
  - Bloques de código explicativos (code { ... }).
  - Bloques de comando ejecutable (cmd { ... }).

### 3.2 Visualización y Formato en el Historial de Chat
- Texto Conversacional: Renderizado estándar como texto legible dentro de la burbuja de chat.
- Bloques code: Formateados con tipografía monoespaciada, fondo diferenciado y contenedor de código sin opciones de ejecución directa.
- Bloques cmd: Renderizados con tarjetas o contenedores visuales de comandos destacados (estilo terminal/CLI).

### 3.3 Indicadores de Estado de Comandos
- Para cada propuesta de comando detectada en la UI, reflejar visualmente su estado correspondiente enviado desde el servidor WebSocket:
  - pending_approval: Estado en espera de decisión en la CLI.
  - success: Comando ejecutado con código de retorno 0 (incluyendo vista previa de la salida).
  - error: Comando fallido o con código de retorno distinto de 0.
  - rejected: Comando rechazado desde la CLI por el usuario.
  - parse_error: Error de sintaxis en el bloque de comando.

---

## 4. Requisitos No Funcionales y Seguridad
1. Aislamiento de Autorización: La interfaz del popup únicamente renderiza y visualiza las propuestas y sus estados. La autorización y ejecución de comandos sigue siendo responsabilidad exclusiva del usuario en la máquina local a través del servidor/CLI.
2. Escapado de Contenido (XSS): Todo el texto entrante, código y salidas de comandos deben sanitizarse o insertarse usando textContent para evitar la ejecución involuntaria de scripts HTML/JS en la UI del popup.
3. Consistencia de Estado: La UI debe synchronizar la lista de mensajes recibida vía POPUP_STREAM_CHAT o eventos de broadcast de WebSocket (COMMAND_PROPOSAL, COMMAND_RESULT).

---

## 5. Estructura de Archivos Afectados
- popup.html: Actualización de la estructura HTML para dar soporte a nuevos componentes visuales de comandos y salidas.
- popup.js: Implementación del parser balanceado de llaves y lógica de renderizado dinámico de tarjetas de comandos.
- REQUISITOS_MEJORA_UI_POPUP.md: Documento descriptivo del proyecto.
