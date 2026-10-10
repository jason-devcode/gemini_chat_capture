"use strict";

/**
 * ============================================================
 * CONFIGURATION
 * ============================================================
 */

const DEFAULT_WS_HOST = "127.0.0.1";
const DEFAULT_WS_PORT = "8765";
const WS_RECONNECT_DELAY_MS = 3000;

const CHAT_WINDOW_CONFIG = {
  width: 380,
  height: 620,
  type: "popup",
  focused: true,
};

const MESSAGE_TYPES = Object.freeze({
  CHAT_UPDATE: "GEMINI_CHAT_UPDATE",
  FINAL_RESPONSE: "GEMINI_FINAL_RESPONSE",
  GET_WS_STATUS: "GET_WS_STATUS",
  RECONNECT_WS: "RECONNECT_WS",
  UPDATE_WS_CONFIG: "UPDATE_WS_CONFIG",
  WS_STATUS_CHANGE: "WS_STATUS_CHANGE",
  POPUP_STREAM_CHAT: "POPUP_STREAM_CHAT",
  CLI_PROMPT: "CLI_PROMPT",
  COMMAND_RESULT: "COMMAND_RESULT",
  FILE_OPERATION_RESULT: "FILE_OPERATION_RESULT",       // <- AÑADIDO
  SEND_GEMINI_PROMPT: "SEND_GEMINI_PROMPT",
});

/**
 * ============================================================
 * LOGGER
 * ============================================================
 */

class Logger {
  static log(level, component, message, details = null) {
    const timestamp = new Date().toISOString();
    const prefix = `[${timestamp}] [${level}] [${component}]`;

    const method =
      level === "ERROR"
        ? console.error
        : level === "WARN"
          ? console.warn
          : console.log;

    if (details !== null) {
      method(`${prefix} ${message}`, details);
      return;
    }

    method(`${prefix} ${message}`);
  }

  static debug(component, message, details = null) {
    this.log("DEBUG", component, message, details);
  }

  static info(component, message, details = null) {
    this.log("INFO", component, message, details);
  }

  static warn(component, message, details = null) {
    this.log("WARN", component, message, details);
  }

  static error(component, message, error = null) {
    const details = error
      ? {
          name: error.name || "Error",
          message: error.message || String(error),
          stack: error.stack || null,
        }
      : null;

    this.log("ERROR", component, message, details);
  }
}

/**
 * ============================================================
 * STORAGE SERVICE
 * ============================================================
 */

class StorageService {
  static get(keys) {
    return new Promise((resolve, reject) => {
      chrome.storage.local.get(keys, (result) => {
        const error = chrome.runtime.lastError;

        if (error) {
          reject(new Error(error.message));
          return;
        }

        resolve(result);
      });
    });
  }

  static set(values) {
    return new Promise((resolve, reject) => {
      chrome.storage.local.set(values, () => {
        const error = chrome.runtime.lastError;

        if (error) {
          reject(new Error(error.message));
          return;
        }

        resolve();
      });
    });
  }

  static async load_ws_config() {
    const config = await this.get(["wsHost", "wsPort"]);

    return {
      host: config.wsHost || DEFAULT_WS_HOST,
      port: String(config.wsPort || DEFAULT_WS_PORT),
    };
  }

  static async save_ws_config(host, port) {
    await this.set({
      wsHost: host,
      wsPort: String(port),
    });
  }

  static async save_chat_history(history) {
    await this.set({ chatHistory: history });
  }
}

/**
 * ============================================================
 * MESSAGE SERVICE
 * ============================================================
 */

class MessageService {
  static async send(message) {
    try {
      await chrome.runtime.sendMessage(message);
    } catch (error) {
      Logger.debug("MessageService", "No se pudo entregar el mensaje.", {
        type: message.type,
        reason: error.message,
      });
    }
  }

  static send_response(send_response, response) {
    try {
      send_response(response);
    } catch (error) {
      Logger.warn(
        "MessageService",
        "No se pudo responder al emisor.",
        { message: error.message }
      );
    }
  }
}

/**
 * ============================================================
 * WEBSOCKET MANAGER
 * ============================================================
 */

class WebSocketManager {
  constructor() {
    this.host = DEFAULT_WS_HOST;
    this.port = DEFAULT_WS_PORT;

    this.socket = null;
    this.reconnect_timer = null;
    this.is_connected = false;
    this.last_error = null;
    this.is_stopped = false;
    this.connection_generation = 0;
  }

  get_url() {
    return `ws://${this.host}:${this.port}`;
  }

  get_status() {
    const connected =
      Boolean(
        this.socket &&
        this.socket.readyState === WebSocket.OPEN &&
        this.is_connected
      );

    return {
      connected,
      host: this.host,
      port: this.port,
      error: connected ? null : this.last_error,
    };
  }

  async initialize() {
    try {
      const config = await StorageService.load_ws_config();

      this.host = this.normalize_host(config.host);
      this.port = this.normalize_port(config.port);

      Logger.info("WebSocketManager", "Configuración cargada.", {
        host: this.host,
        port: this.port,
      });

      this.connect();
    } catch (error) {
      Logger.error(
        "WebSocketManager",
        "No se pudo cargar la configuración.",
        error
      );

      this.schedule_reconnect();
    }
  }

  normalize_host(host) {
    const normalized_host = String(host || "").trim();

    if (!normalized_host || normalized_host === "0.0.0.0") {
      return DEFAULT_WS_HOST;
    }

    return normalized_host;
  }

  normalize_port(port) {
    const normalized_port = String(port || "").trim();
    const numeric_port = Number(normalized_port);

    if (
      !Number.isInteger(numeric_port) ||
      numeric_port < 1 ||
      numeric_port > 65535
    ) {
      throw new Error(`Puerto WebSocket inválido: ${normalized_port}`);
    }

    return String(numeric_port);
  }

  async update_config(host, port) {
    const normalized_host = this.normalize_host(host);
    const normalized_port = this.normalize_port(port);

    await StorageService.save_ws_config(
      normalized_host,
      normalized_port
    );

    this.host = normalized_host;
    this.port = normalized_port;
    this.last_error = null;

    this.connect();
  }

  connect() {
    this.is_stopped = false;
    this.clear_reconnect_timer();

    this.connection_generation += 1;
    const generation = this.connection_generation;

    this.close_socket();
    this.is_connected = false;

    const ws_url = this.get_url();

    Logger.info("WebSocketManager", "Intentando conectar.", {
      url: ws_url,
    });

    let socket;

    try {
      socket = new WebSocket(ws_url);
      this.socket = socket;
    } catch (error) {
      this.handle_connection_error(error);
      return;
    }

    socket.addEventListener("open", () => {
      if (!this.is_current_socket(socket, generation)) {
        return;
      }

      this.is_connected = true;
      this.last_error = null;

      Logger.info("WebSocketManager", "Conexión establecida.", {
        url: ws_url,
      });

      this.notify_status();
    });

    socket.addEventListener("message", (event) => {
      if (!this.is_current_socket(socket, generation)) {
        return;
      }

      Logger.debug("WebSocketManager", "Mensaje recibido del servidor.", {
        data_type: typeof event.data,
      });

      try {
        const data = JSON.parse(event.data);

        // 1. Manejo de Prompts libres desde la CLI del servidor
        if (data.type === MESSAGE_TYPES.CLI_PROMPT && data.text) {
          this.forward_to_gemini_tab(data.text);
        }

        // 2. Manejo de Resultados de Comandos ejecutados por el agente
        if (data.type === MESSAGE_TYPES.COMMAND_RESULT) {
          const formattedText = `[Resultado del Comando: ${data.command}]\nEstado: ${data.status}\nSalida:\n${data.output}`;
          this.forward_to_gemini_tab(formattedText);
        }

        // 3. Manejo de Resultados de Operaciones de Ficheros (CORRECCIÓN)
        if (data.type === MESSAGE_TYPES.FILE_OPERATION_RESULT) {
          const actionName = data.operation?.action || "desconocida";
          const formattedText = `[Resultado de Herramienta de Archivo: ${actionName}]\nEstado: ${data.status}\nSalida:\n${data.output}`;
          this.forward_to_gemini_tab(formattedText);
        }
      } catch (err) {
        Logger.error("WebSocketManager", "Error parseando mensaje entrante del servidor", err);
      }
    });

    socket.addEventListener("error", () => {
      if (!this.is_current_socket(socket, generation)) {
        return;
      }

      this.last_error = `Error de conexión WebSocket con ${ws_url}.`;

      Logger.warn("WebSocketManager", this.last_error);
      this.notify_status();
    });

    socket.addEventListener("close", (event) => {
      if (!this.is_current_socket(socket, generation)) {
        return;
      }

      this.is_connected = false;

      if (!this.last_error) {
        this.last_error = `Conexión cerrada con ${ws_url} (código ${event.code}).`;
      }

      Logger.warn("WebSocketManager", "Conexión cerrada.", {
        code: event.code,
        reason: event.reason || "Sin motivo informado",
        was_clean: event.wasClean,
      });

      this.notify_status();
      this.schedule_reconnect();
    });
  }

  is_current_socket(socket, generation) {
    return (
      this.socket === socket &&
      this.connection_generation === generation
    );
  }

  handle_connection_error(error) {
    this.is_connected = false;
    this.last_error = `No se pudo crear la conexión WebSocket: ${error.message}`;

    Logger.error(
      "WebSocketManager",
      "Falló la creación del socket.",
      error
    );

    this.notify_status();
    this.schedule_reconnect();
  }

  forward_to_gemini_tab(text) {
    chrome.tabs.query({ url: "https://gemini.google.com\/*" }, (tabs) => {
      if (tabs && tabs.length > 0) {
        const activeTab = tabs.find((t) => t.active) || tabs[0];
        chrome.tabs.sendMessage(activeTab.id, {
          type: MESSAGE_TYPES.SEND_GEMINI_PROMPT,
          text: text,
        });
      } else {
        Logger.warn("WebSocketManager", "No se encontró ninguna pestaña activa con Gemini.");
      }
    });
  }

  send(data) {
    if (
      !this.socket ||
      this.socket.readyState !== WebSocket.OPEN ||
      !this.is_connected
    ) {
      Logger.warn(
        "WebSocketManager",
        "Mensaje descartado: el socket no está conectado.",
        {
          ready_state: this.socket
            ? this.socket.readyState
            : "NO_SOCKET",
        }
      );

      return false;
    }

    try {
      const serialized_data = JSON.stringify(data);

      if (serialized_data === undefined) {
        throw new Error("El mensaje no se puede serializar como JSON.");
      }

      this.socket.send(serialized_data);

      Logger.info("WebSocketManager", "Mensaje enviado.", {
        type: data?.type || "UNKNOWN",
        bytes: serialized_data.length,
      });

      return true;
    } catch (error) {
      this.last_error = `Error al enviar mensaje: ${error.message}`;

      Logger.error(
        "WebSocketManager",
        "Falló el envío del mensaje.",
        error
      );

      this.notify_status();
      return false;
    }
  }

  schedule_reconnect() {
    if (this.is_stopped || this.reconnect_timer !== null) {
      return;
    }

    Logger.info(
      "WebSocketManager",
      "Programando reconexión.",
      { delay_ms: WS_RECONNECT_DELAY_MS }
    );

    this.reconnect_timer = setTimeout(() => {
      this.reconnect_timer = null;
      this.connect();
    }, WS_RECONNECT_DELAY_MS);
  }

  clear_reconnect_timer() {
    if (this.reconnect_timer !== null) {
      clearTimeout(this.reconnect_timer);
      this.reconnect_timer = null;
    }
  }

  close_socket() {
    const socket = this.socket;
    this.socket = null;
    this.is_connected = false;

    if (!socket) {
      return;
    }

    try {
      if (
        socket.readyState === WebSocket.CONNECTING ||
        socket.readyState === WebSocket.OPEN
      ) {
        socket.close(1000, "Reemplazando conexión");
      }
    } catch (error) {
      Logger.warn(
        "WebSocketManager",
        "No se pudo cerrar el socket anterior.",
        { message: error.message }
      );
    }
  }

  async notify_status() {
    await MessageService.send({
      type: MESSAGE_TYPES.WS_STATUS_CHANGE,
      ...this.get_status(),
    });
  }

  reconnect() {
    this.last_error = null;
    this.connect();
  }

  stop() {
    this.is_stopped = true;
    this.clear_reconnect_timer();
    this.connection_generation += 1;
    this.close_socket();

    Logger.info("WebSocketManager", "Gestor detenido.");
  }
}

/**
 * ============================================================
 * CHAT HISTORY SERVICE
 * ============================================================
 */

class ChatHistoryService {
  constructor() {
    this.history = [];
  }

  async initialize() {
    try {
      const stored_data = await StorageService.get(["chatHistory"]);

      this.history = Array.isArray(stored_data.chatHistory)
        ? stored_data.chatHistory
        : [];

      Logger.info("ChatHistoryService", "Historial cargado.", {
        entries: this.history.length,
      });
    } catch (error) {
      Logger.error(
        "ChatHistoryService",
        "No se pudo cargar el historial.",
        error
      );
    }
  }

  async update(history) {
    if (!Array.isArray(history)) {
      throw new TypeError("El historial recibido debe ser un array.");
    }

    this.history = history;
    await StorageService.save_chat_history(this.history);

    await MessageService.send({
      type: MESSAGE_TYPES.POPUP_STREAM_CHAT,
      history: this.history,
    });

    Logger.debug("ChatHistoryService", "Historial actualizado.", {
      entries: this.history.length,
    });
  }
}

/**
 * ============================================================
 * CHAT WINDOW MANAGER
 * ============================================================
 */

class ChatWindowManager {
  constructor() {
    this.window_id = null;
    this.is_creating = false;
  }

  async open() {
    if (this.window_id !== null) {
      try {
        await chrome.windows.update(this.window_id, {
          focused: true,
        });
        return;
      } catch (error) {
        Logger.warn(
          "ChatWindowManager",
          "No se pudo enfocar la ventana existente.",
          { message: error.message }
        );
        this.window_id = null;
      }
    }

    await this.create();
  }

  async create() {
    if (this.is_creating) {
      return;
    }

    this.is_creating = true;

    try {
      const window = await chrome.windows.create({
        url: chrome.runtime.getURL("popup.html"),
        ...CHAT_WINDOW_CONFIG,
      });

      if (!window || window.id === undefined) {
        throw new Error("Chrome no devolvió un identificador de ventana.");
      }

      this.window_id = window.id;

      Logger.info("ChatWindowManager", "Ventana creada.", {
        window_id: this.window_id,
      });
    } catch (error) {
      Logger.error(
        "ChatWindowManager",
        "No se pudo crear la ventana del chat.",
        error
      );
    } finally {
      this.is_creating = false;
    }
  }

  handle_window_removed(closed_window_id) {
    if (closed_window_id === this.window_id) {
      this.window_id = null;
      Logger.debug("ChatWindowManager", "Ventana del chat cerrada.");
    }
  }
}

/**
 * ============================================================
 * MESSAGE ROUTER
 * ============================================================
 */

class MessageRouter {
  constructor(ws_manager, chat_history_service) {
    this.ws_manager = ws_manager;
    this.chat_history_service = chat_history_service;
  }

  handle(message, sender, send_response) {
    if (!message || typeof message.type !== "string") {
      Logger.warn("MessageRouter", "Mensaje inválido recibido.");
      return false;
    }

    switch (message.type) {
      case MESSAGE_TYPES.CHAT_UPDATE:
        this.handle_chat_update(message);
        return false;

      case MESSAGE_TYPES.FINAL_RESPONSE:
        this.handle_final_response(message);
        return false;

      case MESSAGE_TYPES.GET_WS_STATUS:
        MessageService.send_response(
          send_response,
          this.ws_manager.get_status()
        );
        return false;

      case MESSAGE_TYPES.RECONNECT_WS:
        this.ws_manager.reconnect();
        MessageService.send_response(send_response, {
          status: "connecting",
        });
        return false;

      case MESSAGE_TYPES.UPDATE_WS_CONFIG:
        return this.handle_config_update(message, send_response);

      default:
        Logger.debug("MessageRouter", "Tipo de mensaje ignorado.", {
          type: message.type,
        });
        return false;
    }
  }

  async handle_chat_update(message) {
    try {
      await this.chat_history_service.update(message.history);
    } catch (error) {
      Logger.error(
        "MessageRouter",
        "No se pudo procesar la actualización del chat.",
        error
      );
    }
  }

  handle_final_response(message) {
    const sent = this.ws_manager.send(message);

    if (!sent) {
      Logger.warn(
        "MessageRouter",
        "La respuesta final no se entregó al servidor."
      );
    }
  }

  handle_config_update(message, send_response) {
    const update = async () => {
      try {
        await this.ws_manager.update_config(
          message.host,
          message.port
        );

        MessageService.send_response(send_response, {
          status: "config_updated",
        });
      } catch (error) {
        Logger.error(
          "MessageRouter",
          "No se pudo actualizar la configuración WebSocket.",
          error
        );

        MessageService.send_response(send_response, {
          status: "error",
          error: error.message,
        });
      }
    };

    void update();
    return true;
  }
}

/**
 * ============================================================
 * APPLICATION
 * ============================================================
 */

class BackgroundApplication {
  constructor() {
    this.ws_manager = new WebSocketManager();
    this.chat_history_service = new ChatHistoryService();
    this.chat_window_manager = new ChatWindowManager();

    this.message_router = new MessageRouter(
      this.ws_manager,
      this.chat_history_service
    );
  }

  async initialize() {
    this.register_listeners();

    chrome.alarms.create("keepAlive", { periodInMinutes: 0.5 });
    chrome.alarms.onAlarm.addListener((alarm) => {
      if (alarm.name === "keepAlive" && !this.ws_manager.get_status().connected) {
        this.ws_manager.connect();
      }
    });

    await Promise.all([
      this.ws_manager.initialize(),
      this.chat_history_service.initialize(),
    ]);

    Logger.info("BackgroundApplication", "Aplicación inicializada.");
  }

  register_listeners() {
    chrome.action.onClicked.addListener(() => {
      void this.chat_window_manager.open();
    });

    chrome.windows.onRemoved.addListener((window_id) => {
      this.chat_window_manager.handle_window_removed(window_id);
    });

    chrome.runtime.onMessage.addListener(
      (message, sender, send_response) => {
        try {
          return this.message_router.handle(
            message,
            sender,
            send_response
          );
        } catch (error) {
          Logger.error(
            "BackgroundApplication",
            "Error procesando un mensaje.",
            error
          );

          MessageService.send_response(send_response, {
            status: "error",
            error: "No se pudo procesar el mensaje.",
          });

          return false;
        }
      }
    );
  }
}

/**
 * ============================================================
 * ENTRY POINT
 * ============================================================
 */

const background_application = new BackgroundApplication();

void background_application.initialize().catch((error) => {
  Logger.error(
    "BackgroundApplication",
    "Error fatal durante la inicialización.",
    error
  );
});
