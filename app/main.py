
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import json
import os
import pathlib

# Get the directory of the current file
current_dir = pathlib.Path(__file__).parent

app = FastAPI()

# Mount static files with the correct relative path
app.mount("/static", StaticFiles(directory=current_dir / "static"), name="static")

# Configure Jinja2 templates to look in the 'templates' directory
templates = Jinja2Templates(directory=current_dir / "templates")

# Dictionary to hold active WebSocket connections
active_connections: dict[str, WebSocket] = {}

# The root endpoint serves the main HTML page using a Jinja2 template
@app.get("/", response_class=HTMLResponse)
async def get(request: Request):
    # Determine the correct WebSocket protocol (wss for HTTPS)
    ws_protocol = "wss" if request.url.is_secure else "ws"
    # Get the host from the request headers, which includes the dev tunnels domain and port
    ws_host = request.headers.get("host")
    # Construct the full WebSocket URL
    ws_url = f"{ws_protocol}://{ws_host}/ws"
    
    return templates.TemplateResponse("index.html", {"request": request, "ws_url": ws_url})

# WebSocket endpoint for real-time signaling
@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    # Accept the connection and add the client to the dictionary
    await websocket.accept()
    active_connections[client_id] = websocket
    print(f"Client {client_id} connected.")

    try:
        while True:
            # Wait for a message from the client
            data = await websocket.receive_text()
            message = json.loads(data)
            
            print(f"Received message from {client_id}: {message}")

            # Check if the message has a target
            target_id = message.get("target_id")
            if not target_id:
                print("Message has no target_id, ignoring.")
                continue

            # Find the target's WebSocket connection
            target_websocket = active_connections.get(target_id)
            if target_websocket:
                # Add the sender's ID to the message before forwarding
                message["sender_id"] = client_id
                # Forward the message to the target
                await target_websocket.send_text(json.dumps(message))
            else:
                # Inform the sender that the target is not online
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "message": f"User {target_id} is not online."
                }))
                print(f"User {target_id} not found.")

    except WebSocketDisconnect:
        # Remove the client from the dictionary on disconnection
        del active_connections[client_id]
        print(f"Client {client_id} disconnected.")
    except Exception as e:
        print(f"An error occurred: {e}")
        # Ensure the connection is closed on any error
        if client_id in active_connections:
            del active_connections[client_id]
