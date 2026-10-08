const chatContainer = document.getElementById("chat-container");
const clearBtn = document.getElementById("clear-btn");
const statusDot = document.querySelector(".status-dot");
const chatInput = document.getElementById("chat-input");
const sendBtn = document.getElementById("send-btn");

function updateWsStatus(isConnected) {
  if (statusDot) {
    statusDot.style.backgroundColor = isConnected ? "#00a884" : "#ef4444";
    statusDot.title = isConnected ? "WebSocket Conectado" : "WebSocket Desconectado";
  }
}

function renderChat(history) {
  chatContainer.innerHTML = "";

  if (!history || history.length === 0) {
    chatContainer.innerHTML = `<div style="text-align:center; color:#8696a0; font-size:12px; margin-top:20px;">Esperando interacción en Gemini...</div>`;
    return;
  }

  history.forEach((msg) => {
    const msgDiv = document.createElement("div");
    msgDiv.classList.add("message", msg.sender);

    const labelDiv = document.createElement("div");
    labelDiv.classList.add("sender-label");
    labelDiv.textContent = msg.sender === "user" ? "Tú" : "Gemini";

    const textSpan = document.createElement("span");
    textSpan.textContent = msg.text;

    msgDiv.appendChild(labelDiv);
    msgDiv.appendChild(textSpan);
    chatContainer.appendChild(msgDiv);
  });

  chatContainer.scrollTop = chatContainer.scrollHeight;
}

function sendMessageToTab(tabId, text) {
  // Asegurarnos de que el content script está inyectado antes de enviar mensaje
  chrome.tabs.sendMessage(tabId, { type: "SEND_GEMINI_PROMPT", text: text }, (response) => {
    if (chrome.runtime.lastError) {
      // Si el listener no responde, re-inyectamos content.js y reintentamos
      chrome.scripting.executeScript({
        target: { tabId: tabId },
        files: ["content.js"]
      }, () => {
        setTimeout(() => {
          chrome.tabs.sendMessage(tabId, { type: "SEND_GEMINI_PROMPT", text: text });
        }, 300);
      });
    }
  });
  chatInput.value = "";
}

function handleSendMessage() {
  const text = chatInput.value.trim();
  if (!text) return;

  chrome.tabs.query({ url: "https://gemini.google.com/*" }, (tabs) => {
    if (tabs && tabs.length > 0) {
      // Buscar la pestaña activa o usar la primera encontrada
      const activeGeminiTab = tabs.find(t => t.active) || tabs[0];
      sendMessageToTab(activeGeminiTab.id, text);
    } else {
      alert("No se encontró ninguna pestaña abierta con Gemini (https://gemini.google.com).");
    }
  });
}

// Escuchadores
sendBtn.addEventListener("click", handleSendMessage);

chatInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    handleSendMessage();
  }
});

// Cargar historial inicial y consultar estado WS
chrome.storage.local.get(["chatHistory"], (result) => {
  renderChat(result.chatHistory || []);
});

chrome.runtime.sendMessage({ type: "GET_WS_STATUS" }, (response) => {
  if (response) updateWsStatus(response.connected);
});

clearBtn.addEventListener("click", () => {
  chrome.storage.local.set({ chatHistory: [] }, () => {
    renderChat([]);
  });
});

chrome.runtime.onMessage.addListener((message) => {
  if (message.type === "POPUP_STREAM_CHAT") {
    renderChat(message.history);
  }
  if (message.type === "WS_STATUS_CHANGE") {
    updateWsStatus(message.connected);
  }
});
