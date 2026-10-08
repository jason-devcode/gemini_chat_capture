const chatContainer = document.getElementById("chat-container");

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

// Cargar historial inicial
chrome.storage.local.get(["chatHistory"], (result) => {
  renderChat(result.chatHistory || []);
});

// Escuchar actualizaciones dinámicas
chrome.runtime.onMessage.addListener((message) => {
  if (message.type === "POPUP_STREAM_CHAT") {
    renderChat(message.history);
  }
});
