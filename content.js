let lastHistoryJSON = "";
let geminiDebounceTimer = null;
const STREAM_TIMEOUT_MS = 1500;

function isExtensionContextValid() {
  return typeof chrome !== "undefined" && chrome.runtime && !!chrome.runtime.id;
}

function cleanUserText(rawText) {
  if (!rawText) return "";

  let text = rawText
    .replace(/^(Tú dijiste|Tú dijiste:)\s*/gi, "")
    .replace(/\s+/g, " ")
    .trim();

  if (!text) return "";

  const halfLen = Math.floor(text.length / 2);
  for (let offset = -3; offset <= 3; offset++) {
    const len = halfLen + offset;
    if (len > 3 && len < text.length) {
      const part1 = text.substring(0, len).trim();
      const part2 = text.substring(len).trim();
      if (part1 === part2) return part1;
    }
  }

  const sampleLength = Math.min(20, Math.floor(text.length / 3));
  if (sampleLength >= 5) {
    const sample = text.substring(0, sampleLength);
    const secondIndex = text.indexOf(sample, sampleLength);
    if (secondIndex !== -1) return text.substring(secondIndex).trim();
  }

  const words = text.split(" ");
  if (words.length >= 6) {
    const wordBlockSize = Math.min(4, Math.floor(words.length / 2));
    const wordSample = words.slice(0, wordBlockSize).join(" ");
    const remainingText = words.slice(wordBlockSize).join(" ");
    const matchIndex = remainingText.toLowerCase().indexOf(wordSample.toLowerCase());

    if (matchIndex !== -1) {
      const cutCharIndex = text.indexOf(wordSample, wordBlockSize);
      if (cutCharIndex !== -1) return text.substring(cutCharIndex).trim();
    }
  }

  if (text.includes("...")) {
    const parts = text.split(/\.\.\.\s*/);
    text = parts.reduce((longest, current) => 
      current.trim().length > longest.trim().length ? current.trim() : longest
    , "");
  }

  return text;
}

function extractUserPrompt(userNode) {
  const queryTextEl = userNode.querySelector('.query-text, [class*="query-content"]');
  if (queryTextEl) return cleanUserText(queryTextEl.textContent);

  const paragraphs = userNode.querySelectorAll('p');
  if (paragraphs.length > 0) {
    const lastParagraphText = paragraphs[paragraphs.length - 1].textContent;
    const cleaned = cleanUserText(lastParagraphText);
    if (cleaned) return cleaned;
  }

  return cleanUserText(userNode.innerText || userNode.textContent || "");
}

function extractGeminiResponse(modelNode) {
  const clone = modelNode.cloneNode(true);
  const unwanted = clone.querySelectorAll('button, mat-icon, .action-buttons, .edit-container, h2, .screenreader-only');
  unwanted.forEach(el => el.remove());

  return (clone.innerText || clone.textContent || "").trim();
}

function buildChatHistory() {
  const history = [];
  const userNodes = Array.from(document.querySelectorAll('user-query'));
  const modelNodes = Array.from(document.querySelectorAll('model-response'));

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

function safeSendMessage(message) {
  if (!isExtensionContextValid()) return;
  try {
    chrome.runtime.sendMessage(message).catch(() => {});
  } catch (e) {}
}

function isGeminiGenerating(lastModelNode) {
  if (!lastModelNode) return false;

  const stopButton = document.querySelector('button[aria-label*="Stop"], button[aria-label*="Detener"], button[aria-label*="stop"]');
  if (stopButton) return true;

  if (
    lastModelNode.classList.contains("streaming") ||
    lastModelNode.classList.contains("generating") ||
    lastModelNode.getAttribute("aria-busy") === "true"
  ) {
    return true;
  }

  const spinner = lastModelNode.querySelector('.mat-mdc-progress-spinner, [role="progressbar"], .sparkle-loader');
  if (spinner) return true;

  return false;
}

function checkAndProcessGeminiCompletion() {
  const modelNodes = document.querySelectorAll('model-response');
  if (modelNodes.length === 0) return;

  const lastModelNode = modelNodes[modelNodes.length - 1];

  // Si este nodo ya fue marcado como enviado a Python, no hacemos nada
  if (lastModelNode.dataset.wsSent === "true") return;

  if (geminiDebounceTimer) clearTimeout(geminiDebounceTimer);

  geminiDebounceTimer = setTimeout(() => {
    if (!isExtensionContextValid()) return;

    // Verificar si sigue generando
    if (isGeminiGenerating(lastModelNode)) {
      checkAndProcessGeminiCompletion();
      return;
    }

    // Verificar que el nodo tenga texto válido y no haya sido enviado aún
    const text = extractGeminiResponse(lastModelNode);
    if (text && lastModelNode.dataset.wsSent !== "true") {
      // Marcar el nodo en el DOM inmediatamente para evitar envíos concurrentes/duplicados
      lastModelNode.dataset.wsSent = "true";

      safeSendMessage({
        type: "GEMINI_FINAL_RESPONSE",
        sender: "gemini",
        text: text,
        timestamp: new Date().toISOString()
      });
    }
  }, STREAM_TIMEOUT_MS);
}

const observer = new MutationObserver(() => {
  if (!isExtensionContextValid()) {
    observer.disconnect();
    if (geminiDebounceTimer) clearTimeout(geminiDebounceTimer);
    return;
  }

  const history = buildChatHistory();
  const currentJSON = JSON.stringify(history);

  if (currentJSON !== lastHistoryJSON && history.length > 0) {
    lastHistoryJSON = currentJSON;
    safeSendMessage({ type: "GEMINI_CHAT_UPDATE", history: history });
  }

  // Evaluar si la última respuesta de Gemini ha finalizado
  checkAndProcessGeminiCompletion();
});

observer.observe(document.body, {
  childList: true,
  subtree: true,
  characterData: true
});
