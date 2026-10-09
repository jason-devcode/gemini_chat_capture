const chatContainer = document.getElementById("chat-container");
const clearBtn = document.getElementById("clear-btn");
const statusDot = document.getElementById("status-dot");
const wsStatusLabel = document.getElementById("ws-status-label");
const wsErrorBanner = document.getElementById("ws-error-banner");
const reconnectBtn = document.getElementById("reconnect-btn");
const toggleConfigBtn = document.getElementById("toggle-config-btn");
const configPanel = document.getElementById("config-panel");
const wsHostInput = document.getElementById("ws-host-input");
const wsPortInput = document.getElementById("ws-port-input");
const saveConfigBtn = document.getElementById("save-config-btn");
const chatInput = document.getElementById("chat-input");
const sendBtn = document.getElementById("send-btn");

function updateWsStatus(isConnected, host, port, error) {
  // Asegurar booleano estricto
  const connected = Boolean(isConnected);

  if (statusDot) {
    statusDot.style.backgroundColor = connected ? "#00a884" : "#ef4444";
    statusDot.title = connected ? "WebSocket Conectado" : "WebSocket Desconectado";
  }

  if (wsStatusLabel) {
    wsStatusLabel.textContent = connected ? "Conectado" : "Desconectado";
    wsStatusLabel.style.color = connected ? "#00a884" : "#ef4444";
  }

  if (wsErrorBanner) {
    if (!connected) {
      const errorText = error || "No se puede establecer conexión con el servidor local.";
      wsErrorBanner.textContent = `⚠️ Error: ${errorText}`;
      wsErrorBanner.classList.add("visible");
    } else {
      wsErrorBanner.textContent = "";
      wsErrorBanner.classList.remove("visible");
    }
  }

  if (host !== undefined && wsHostInput) wsHostInput.value = host;
  if (port !== undefined && wsPortInput) wsPortInput.value = port;
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
  chrome.tabs.sendMessage(tabId, { type: "SEND_GEMINI_PROMPT", text: text }, (response) => {
    if (chrome.runtime.lastError) {
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

toggleConfigBtn.addEventListener("click", () => {
  configPanel.classList.toggle("open");
});

reconnectBtn.addEventListener("click", () => {
  wsStatusLabel.textContent = "Conectando...";
  wsStatusLabel.style.color = "#eab308";
  chrome.runtime.sendMessage({ type: "RECONNECT_WS" });
});

saveConfigBtn.addEventListener("click", () => {
  const newHost = wsHostInput.value.trim() || "0.0.0.0";
  const newPort = wsPortInput.value.trim() || "8765";

  wsStatusLabel.textContent = "Conectando...";
  wsStatusLabel.style.color = "#eab308";

  chrome.runtime.sendMessage({
    type: "UPDATE_WS_CONFIG",
    host: newHost,
    port: newPort
  }, () => {
    configPanel.classList.remove("open");
  });
});

chrome.storage.local.get(["chatHistory", "wsHost", "wsPort"], (result) => {
  renderChat(result.chatHistory || []);
  if (result.wsHost) wsHostInput.value = result.wsHost;
  if (result.wsPort) wsPortInput.value = result.wsPort;
});

// Consulta inicial del estado real del WS
chrome.runtime.sendMessage({ type: "GET_WS_STATUS" }, (response) => {
  if (response) {
    updateWsStatus(response.connected, response.host, response.port, response.error);
  }
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
    updateWsStatus(message.connected, message.host, message.port, message.error);
  }
});
