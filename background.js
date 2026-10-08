let currentChatHistory = [];
let windowId = null;
let ws = null;
let wsReconnectTimer = null;
let isWsConnected = false;

function connectWebSocket() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
    return;
  }

  try {
    ws = new WebSocket("ws://127.0.0.1:8765");

    ws.onopen = () => {
      console.log("[Background WS] Conectado exitosamente al servidor Python.");
      isWsConnected = true;
      notifyPopupWsStatus(true);
    };

    ws.onclose = () => {
      if (isWsConnected) {
        console.warn("[Background WS] Conexión cerrada. Reintentando en 3s...");
      }
      isWsConnected = false;
      notifyPopupWsStatus(false);

      if (wsReconnectTimer) clearTimeout(wsReconnectTimer);
      wsReconnectTimer = setTimeout(connectWebSocket, 3000);
    };

    ws.onerror = () => {
      // Reemplazamos console.error por console.warn para evitar el rastreo de error en DevTools
      console.warn("[Background WS] Servidor no disponible en ws://127.0.0.1:8765. Esperando reconexión...");
      if (ws) ws.close();
    };
  } catch (e) {
    console.warn("[Background WS] Excepción al intentar instanciar WebSocket:", e);
  }
}

function sendToWebSocket(data) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(data));
  } else {
    console.warn("[Background WS] No se pudo enviar el mensaje: WS no conectado.");
  }
}

function notifyPopupWsStatus(connected) {
  chrome.runtime.sendMessage({
    type: "WS_STATUS_CHANGE",
    connected: connected
  }).catch(() => {});
}

connectWebSocket();

chrome.action.onClicked.addListener(() => {
  if (windowId !== null) {
    chrome.windows.update(windowId, { focused: true }).catch(() => {
      windowId = null;
      createChatWindow();
    });
  } else {
    createChatWindow();
  }
});

function createChatWindow() {
  chrome.windows.create({
    url: chrome.runtime.getURL("popup.html"),
    type: "popup",
    width: 380,
    height: 560,
    focused: true
  }, (win) => {
    windowId = win.id;
  });
}

chrome.windows.onRemoved.addListener((closedWindowId) => {
  if (closedWindowId === windowId) {
    windowId = null;
  }
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "GEMINI_CHAT_UPDATE") {
    currentChatHistory = message.history;
    chrome.storage.local.set({ chatHistory: currentChatHistory });

    chrome.runtime.sendMessage({
      type: "POPUP_STREAM_CHAT",
      history: currentChatHistory
    }).catch(() => {});
  }

  if (message.type === "GEMINI_FINAL_RESPONSE") {
    sendToWebSocket(message);
  }

  if (message.type === "GET_WS_STATUS") {
    sendResponse({ connected: isWsConnected });
  }
});
