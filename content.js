let lastHistoryJSON = "";

/**
 * CAPA DE LIMPIEZA MULTI-GUARD
 * Combina múltiples estrategias para garantizar que solo se devuelva
 * la versión final completa de la consulta, ignorando borradores o duplicados.
 */
function cleanUserText(rawText) {
  if (!rawText) return "";

  // Guard 0: Limpieza base de espacios y prefijos de accesibilidad
  let text = rawText
    .replace(/^(Tú dijiste|Tú dijiste:)\s*/gi, "")
    .replace(/\s+/g, " ")
    .trim();

  if (!text) return "";

  // GUARD 1: Mitades exactas (Ej: "Hola mundo Hola mundo")
  const halfLen = Math.floor(text.length / 2);
  for (let offset = -3; offset <= 3; offset++) {
    const len = halfLen + offset;
    if (len > 3 && len < text.length) {
      const part1 = text.substring(0, len).trim();
      const part2 = text.substring(len).trim();
      if (part1 === part2) {
        return part1;
      }
    }
  }

  // GUARD 2: Búsqueda por Muestra Inicial (Substring Sample)
  // Toma los primeros 15-20 caracteres y busca si vuelven a aparecer más adelante
  const sampleLength = Math.min(20, Math.floor(text.length / 3));
  if (sampleLength >= 5) {
    const sample = text.substring(0, sampleLength);
    const secondIndex = text.indexOf(sample, sampleLength);
    if (secondIndex !== -1) {
      return text.substring(secondIndex).trim();
    }
  }

  // GUARD 3: Comparación de N-Gramas de palabras (Word Blocks)
  // Útil si hay ligeras variaciones de caracteres al inicio del borrador
  const words = text.split(" ");
  if (words.length >= 6) {
    const wordBlockSize = Math.min(4, Math.floor(words.length / 2));
    const wordSample = words.slice(0, wordBlockSize).join(" ");
    const remainingText = words.slice(wordBlockSize).join(" ");
    const matchIndex = remainingText.toLowerCase().indexOf(wordSample.toLowerCase());

    if (matchIndex !== -1) {
      const cutCharIndex = text.indexOf(wordSample, wordBlockSize);
      if (cutCharIndex !== -1) {
        return text.substring(cutCharIndex).trim();
      }
    }
  }

  // GUARD 4: Manejo de Elipsis / Truncamiento ("texto corto... texto completo")
  if (text.includes("...")) {
    const parts = text.split(/\.\.\.\s*/);
    text = parts.reduce((longest, current) => 
      current.trim().length > longest.trim().length ? current.trim() : longest
    , "");
  }

  return text;
}

/**
 * CAPA DE EXTRACCIÓN DOM (Guard de Selección de Nodo)
 */
function extractUserPrompt(userNode) {
  // Intentar leer directo desde contenedores específicos si existen en esta versión de la UI
  const queryTextEl = userNode.querySelector('.query-text, [class*="query-content"]');
  if (queryTextEl) {
    return cleanUserText(queryTextEl.textContent);
  }

  const paragraphs = userNode.querySelectorAll('p');
  if (paragraphs.length > 0) {
    const lastParagraphText = paragraphs[paragraphs.length - 1].textContent;
    const cleaned = cleanUserText(lastParagraphText);
    if (cleaned) return cleaned;
  }

  // Fallback a texto general del nodo pasado por la tubería de Guards
  const rawText = userNode.innerText || userNode.textContent || "";
  return cleanUserText(rawText);
}

function extractGeminiResponse(modelNode) {
  const clone = modelNode.cloneNode(true);

  // Eliminar botones, iconos y elementos auxiliares de interfaz
  const unwanted = clone.querySelectorAll('button, mat-icon, .action-buttons, .edit-container, h2, .screenreader-only');
  unwanted.forEach(el => el.remove());

  return (clone.innerText || clone.textContent || "").trim();
}

function buildChatHistory() {
  const history = [];
  const userNodes = Array.from(document.querySelectorAll('user-query'));
  const modelNodes = Array.from(document.querySelectorAll('model-response'));

  // Ordenar cronológicamente por su posición en el DOM
  const allNodes = [...userNodes, ...modelNodes].sort((a, b) => {
    return (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING) ? -1 : 1;
  });

  allNodes.forEach((node) => {
    const isUser = node.tagName.toLowerCase() === 'user-query';
    const text = isUser ? extractUserPrompt(node) : extractGeminiResponse(node);

    if (text) {
      const sender = isUser ? 'user' : 'gemini';
      const last = history[history.length - 1];

      if (!last || last.sender !== sender || last.text !== text) {
        history.push({ sender, text });
      }
    }
  });

  return history;
}

function safeSendMessage(history) {
  if (typeof chrome === "undefined" || !chrome.runtime || !chrome.runtime.id) {
    return;
  }

  chrome.runtime.sendMessage({
    type: "GEMINI_CHAT_UPDATE",
    history: history
  }).catch(() => {});
}

const observer = new MutationObserver(() => {
  const history = buildChatHistory();
  const currentJSON = JSON.stringify(history);

  if (currentJSON !== lastHistoryJSON && history.length > 0) {
    lastHistoryJSON = currentJSON;
    safeSendMessage(history);
  }
});

observer.observe(document.body, {
  childList: true,
  subtree: true,
  characterData: true
});
