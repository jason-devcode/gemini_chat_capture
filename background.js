let currentChatHistory = [];
let windowId = null;

// Abrir ventana flotante al pulsar el icono de la extensión
chrome.action.onClicked.addListener(() => {
  if (windowId !== null) {
    // Si la ventana ya existe, la traemos al frente
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

// Escuchar actualizaciones del content script
chrome.runtime.onMessage.addListener((message) => {
  if (message.type === "GEMINI_CHAT_UPDATE") {
    currentChatHistory = message.history;
    chrome.storage.local.set({ chatHistory: currentChatHistory });

    chrome.runtime.sendMessage({
      type: "POPUP_STREAM_CHAT",
      history: currentChatHistory
    }).catch(() => {});
  }
});
