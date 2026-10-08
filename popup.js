const chatContainer = document.getElementById("chat-container");
const clearBtn = document.getElementById("clear-btn");
const statusDot = document.querySelector(".status-dot");

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
