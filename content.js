let lastHistoryJSON = "";
let geminiDebounceTimer = null;
const STREAM_TIMEOUT_MS = 1500;

function isExtensionContextValid() {
  return typeof chrome !== "undefined" && chrome.runtime && !!chrome.runtime.id;
}

function cleanUserText(rawText) {
  if (!rawText) return "";

  let text = rawText
    .replace(/^(Tú dijiste|Tú dijiste:|Has dicho)\s*/gi, "")
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

  return text;
}

function extractUserPrompt(userNode) {
  const queryTextEl = userNode.querySelector('.query-text, .query-text-line, [class*="query-content"]');
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
  const unwanted = clone.querySelectorAll('button, mat-icon, .action-buttons, .edit-container, h2, h5, h6, .screenreader-only, .cdk-visually-hidden');
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

  // 1. Verificar si existe un botón de "Detener" o "Stop" activo y visible
  const stopButton = document.querySelector('button[aria-label*="Stop"], button[aria-label*="Detener"], button[aria-label*="stop"]');
  if (stopButton && (stopButton.offsetWidth > 0 || stopButton.offsetHeight > 0)) {
    return true;
  }

  // 2. Verificar si el nodo tiene atributos/clases explícitas de streaming
  if (
    lastModelNode.classList.contains("streaming") ||
    lastModelNode.classList.contains("generating") ||
    lastModelNode.getAttribute("aria-busy") === "true"
  ) {
    return true;
  }

  // 3. Verificar si hay spinners de carga activos y visibles
  const spinner = lastModelNode.querySelector('.mat-mdc-progress-spinner, [role="progressbar"]');
  if (spinner && (spinner.offsetWidth > 0 || spinner.offsetHeight > 0)) {
    return true;
  }

  return false;
}

function checkAndProcessGeminiCompletion() {
  const modelNodes = document.querySelectorAll('model-response');
  if (modelNodes.length === 0) return;

  const lastModelNode = modelNodes[modelNodes.length - 1];

  if (lastModelNode.dataset.wsSent === "true") return;

  if (geminiDebounceTimer) clearTimeout(geminiDebounceTimer);

  geminiDebounceTimer = setTimeout(() => {
    if (!isExtensionContextValid()) return;

    if (isGeminiGenerating(lastModelNode)) {
      checkAndProcessGeminiCompletion();
      return;
    }

    const text = extractGeminiResponse(lastModelNode);
    if (text && text.length > 0 && lastModelNode.dataset.wsSent !== "true") {
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

// Inyección del prompt en el editor de Gemini (modificado para soportar multilínea y comandos)
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "SEND_GEMINI_PROMPT" && message.text) {
    const inputArea = document.querySelector('.ql-editor, rich-textarea div[contenteditable="true"], div[contenteditable="true"], textarea');
    
    if (inputArea) {
      inputArea.focus();
      
      if (inputArea.tagName.toLowerCase() === 'textarea') {
        inputArea.value = message.text;
      } else {
        // Formatear saltos de línea para elementos contenteditable[cite: 3]
        const formattedHTML = message.text.replace(/\n/g, '<br>');
        inputArea.innerHTML = `<p>${formattedHTML}</p>`;
      }
      
      inputArea.dispatchEvent(new Event('input', { bubbles: true }));
      inputArea.dispatchEvent(new Event('change', { bubbles: true }));

      setTimeout(() => {
        const sendBtn = document.querySelector('button[aria-label*="Enviar"], button[aria-label*="Send"], button.send-button');
        if (sendBtn) {
          sendBtn.click();
        } else {
          inputArea.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', keyCode: 13, bubbles: true }));
        }
      }, 200);

      if (sendResponse) sendResponse({ status: "success" });
    } else {
      if (sendResponse) sendResponse({ status: "input_not_found" });
    }
  }
  return true;
});

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

  checkAndProcessGeminiCompletion();
});

observer.observe(document.body, {
  childList: true,
  subtree: true,
  characterData: true
});
