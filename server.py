import asyncio
import json
from datetime import datetime

import websockets

HOST = "localhost"
PORT = 8765


async def handle_client(websocket):
    client_address = websocket.remote_address
    print(f"[\031[32m+\033[0m] Cliente conectado desde {client_address}")

    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                msg_type = data.get("type")

                if msg_type == "GEMINI_FINAL_RESPONSE":
                    sender = data.get("sender", "gemini").upper()
                    text = data.get("text", "")
                    timestamp = data.get("timestamp", datetime.now().isoformat())

                    print("\n" + "=" * 60)
                    print(f"[\033[34m{timestamp}\033[0m] Mensaje Completo Recibido ({sender}):")
                    print("-" * 60)
                    print(text)
                    print("=" * 60 + "\n")

            except json.JSONDecodeError:
                print(f"[\033[31m!\033[0m] Error al decodificar JSON: {message}")

    except websockets.exceptions.ConnectionClosedOK:
        pass
    except websockets.exceptions.ConnectionClosedError as e:
        print(f"[\033[31m!\033[0m] Conexión cerrada con error: {e}")
    finally:
        print(f"[\033[31m-\033[0m] Cliente desconectado {client_address}")


async def main():
    print(f"[\033[36m*\033[0m] Iniciando servidor WebSocket en ws://{HOST}:{PORT}")
    async with websockets.serve(handle_client, HOST, PORT):
        await asyncio.Future()  # Mantiene el servidor ejecutándose


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[\033[33m!\033[0m] Servidor detenido por el usuario.")
