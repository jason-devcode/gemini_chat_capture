let lastHistoryJSON = "";

function extractTextContent(element) {
  if (!element) return "";
  // Clonamos para remover sugerencias o botones internos si existieran
  const clone = element.cloneNode(true);
  
  // Limpiamos sub-elementos de UI no deseados dentro de la respuesta
  const unneeded = clone.querySelectorAll('button, .edit-container, .action-buttons, mat-icon');
  unneeded.forEach(el => el.remove());

  return clone.innerText ? clone.innerText.trim() : clone.textContent.trim();
}

function getCleanChatHistory() {
  const chatHistory = [];

  // Seleccionamos los contenedores principales de los giros de conversación
  // Gemini suele usar 'user-query' y 'model-response'
  const userNodes = Array.from(document.querySelectorAll('user-query'));
  const modelNodes = Array.from(document.querySelectorAll('model-response'));

  // Unimos y ordenamos los nodos según su posición real en el DOM
  const allNodes = [...userNodes, ...modelNodes].sort((a, b) => {
    return (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING) ? -1 : 1;
  });

  allNodes.forEach((node) => {
    const isUser = node.tagName.toLowerCase() === 'user-query';
    const text = extractTextContent(node);

    if (text) {
      const sender = isUser ? 'user' : 'gemini';

      // Evitamos duplicar exactamente el mismo mensaje consecutivo
      const lastEntry = chatHistory[chatHistory.length - 1];
      if (!lastEntry || lastEntry.sender !== sender || lastEntry.text !== text) {
        chatHistory.push({ sender, text });
      }
    }
  });

  return chatHistory;
}

function observeGeminiChat() {
  const chatContainer = document.body;

  const observer = new MutationObserver(() => {
    const chatHistory = getCleanChatHistory();
    const currentJSON = JSON.stringify(chatHistory);

    if (currentJSON !== lastHistoryJSON && chatHistory.length > 0) {
      lastHistoryJSON = currentJSON;

      chrome.runtime.sendMessage({
        type: "GEMINI_CHAT_UPDATE",
        history: chatHistory
      }).catch(() => {});
    }
  });

  observer.observe(chatContainer, {
    childList: true,
    subtree: true,
    characterData: true
  });
}

observeGeminiChat();
