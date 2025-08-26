from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Form, HTTPException, Depends, status, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import create_engine, Column, String, Boolean, ForeignKey
from sqlalchemy.orm import sessionmaker, declarative_base, relationship
from pydantic import BaseModel
import json
import os
import pathlib
import uuid
import hashlib
import shutil
from typing import List, Optional

# Get the directory of the current file
current_dir = pathlib.Path(__file__).parent

# --- Database Setup (SQLite) ---
DATABASE_URL = "sqlite:///./users.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Define the User model for the database with new fields
class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    password_hash = Column(String)  # Store hashed passwords
    full_name = Column(String)  # New field
    email = Column(String, unique=True, index=True) # New field
    profile_pic = Column(String) # Path to the profile picture file
    is_online = Column(Boolean, default=False)
    
    # Friendship relationships
    friends_of = relationship("Friendship", foreign_keys="Friendship.user_id", back_populates="user")
    user_of = relationship("Friendship", foreign_keys="Friendship.friend_id", back_populates="friend")

# Define a Friendship model to store relationships
class Friendship(Base):
    __tablename__ = "friendships"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"))
    friend_id = Column(String, ForeignKey("users.id"))
    
    # Define relationships for easy lookup
    user = relationship("User", foreign_keys=[user_id])
    friend = relationship("User", foreign_keys=[friend_id])

# Create the database tables
Base.metadata.create_all(bind=engine)

app = FastAPI()

# Mount static files with the correct relative path
app.mount("/static", StaticFiles(directory=current_dir / "static"), name="static")

# Define and create the media folder for file uploads
MEDIA_DIR = current_dir.parent / "media"
if not os.path.exists(MEDIA_DIR):
    os.makedirs(MEDIA_DIR)

# Mount the media folder to serve files
app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")


# Configure Jinja2 templates
templates = Jinja2Templates(directory=current_dir / "templates")

# In-memory dictionary to hold active WebSocket connections
# This is a mapping from the permanent user ID to the WebSocket object
active_connections: dict[str, WebSocket] = {}

# Simple in-memory session store (for demo purposes)
# In a real app, use a proper session library or JWTs.
active_sessions: dict[str, str] = {} # token -> user_id

class UserInDB(BaseModel):
    id: str
    username: str
    full_name: str
    profile_pic: str

class FriendshipData(BaseModel):
    id: str
    full_name: str
    profile_pic: str
    is_online: bool

# --- Helper Functions ---
def hash_password(password: str) -> str:
    """Hashes a password using SHA256."""
    return hashlib.sha256(password.encode()).hexdigest()

def get_user_from_token(token: str):
    """Retrieves a user ID from a token."""
    user_id = active_sessions.get(token)
    if not user_id:
        return None
    db = SessionLocal()
    user = db.query(User).filter(User.id == user_id).first()
    db.close()
    return user

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Function to broadcast online friends list to all active connections
async def broadcast_online_friends(client_id: str):
    db = SessionLocal()
    # Get the user's friends from the Friendship table
    friends = db.query(Friendship).filter(Friendship.user_id == client_id).all()
    friend_ids = [friend.friend_id for friend in friends]
    
    # Find which friends are online from the in-memory active_connections list
    online_friends_data = []
    for friend_id in friend_ids:
        friend_user = db.query(User).filter(User.id == friend_id).first()
        if friend_user:
            online_friends_data.append({
                "id": friend_user.id,
                "full_name": friend_user.full_name,
                "profile_pic": friend_user.profile_pic,
                "is_online": friend_id in active_connections
            })
    db.close()
    
    websocket = active_connections.get(client_id)
    if websocket:
        try:
            await websocket.send_text(json.dumps({
                "type": "online-friends-update",
                "payload": online_friends_data
            }))
        except WebSocketDisconnect:
            pass
        except Exception as e:
            print(f"Error broadcasting to {client_id}: {e}")

async def broadcast_to_all_friends():
    for user_id in active_connections:
        await broadcast_online_friends(user_id)


# --- API Endpoints ---
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    # This endpoint now redirects to the login page by default
    return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.get("/app", response_class=HTMLResponse)
async def main_app(request: Request):
    token = request.cookies.get("session_token")
    user = get_user_from_token(token)
    if not user:
        # Redirect to login page if no valid session
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    ws_protocol = "wss" if request.url.is_secure else "ws"
    ws_host = request.headers.get("host")
    ws_url = f"{ws_protocol}://{ws_host}/ws/{user.id}/{token}"

    return templates.TemplateResponse("index.html", {"request": request, "ws_url": ws_url, "user_id": user.id, "full_name": user.full_name})

@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...), db: SessionLocal = Depends(get_db)):
    user = db.query(User).filter(User.username == username).first()
    
    if not user or user.password_hash != hash_password(password):
        # On failure, redirect back to the login page with an error message
        return templates.TemplateResponse(
            "login.html", 
            {"request": request, "error_message": "Invalid username or password."}
        )
    
    # On success, create a session token and redirect
    token = str(uuid.uuid4())
    active_sessions[token] = user.id
    
    redirect_response = RedirectResponse(url="/app", status_code=status.HTTP_303_SEE_OTHER)
    redirect_response.set_cookie(key="session_token", value=token, httponly=True)
    return redirect_response

@app.post("/logout")
async def logout(request: Request):
    token = request.cookies.get("session_token")
    if token in active_sessions:
        del active_sessions[token]
    
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(key="session_token")
    return response

@app.post("/register")
async def register(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    full_name: str = Form(...),
    email: str = Form(...),
    profile_pic: UploadFile = File(...),
    db: SessionLocal = Depends(get_db)
):
    if db.query(User).filter(User.username == username).first():
        return templates.TemplateResponse(
            "login.html", 
            {"request": request, "error_message": "Username already registered."}
        )
    
    if db.query(User).filter(User.email == email).first():
        return templates.TemplateResponse(
            "login.html", 
            {"request": request, "error_message": "Email already registered."}
        )
    
    # Save the uploaded file
    file_extension = os.path.splitext(profile_pic.filename)[1]
    unique_filename = f"{uuid.uuid4()}{file_extension}"
    file_path = MEDIA_DIR / unique_filename
    
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(profile_pic.file, buffer)
    finally:
        profile_pic.file.close()
    
    hashed_password = hash_password(password)
    user_id = str(uuid.uuid4())
    new_user = User(
        id=user_id, 
        username=username, 
        password_hash=hashed_password, 
        full_name=full_name, 
        email=email, 
        profile_pic=f"/media/{unique_filename}", # Store the relative path
        is_online=False
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    # On successful registration, redirect to the login page with a success message
    return templates.TemplateResponse(
        "login.html", 
        {"request": request, "success_message": "Registration successful! You can now log in."}
    )

@app.get("/friends", response_model=List[FriendshipData])
async def get_friends(request: Request, db: SessionLocal = Depends(get_db)):
    token = request.cookies.get("session_token")
    user = get_user_from_token(token)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    
    friends = db.query(Friendship).filter(Friendship.user_id == user.id).all()
    friend_data = []
    for friendship in friends:
        friend_user = db.query(User).filter(User.id == friendship.friend_id).first()
        if friend_user:
            friend_data.append({
                "id": friend_user.id,
                "full_name": friend_user.full_name,
                "profile_pic": friend_user.profile_pic,
                "is_online": friend_user.id in active_connections
            })
    return friend_data

@app.post("/add_friend")
async def add_friend(request: Request, friend_username: str = Form(...), db: SessionLocal = Depends(get_db)):
    token = request.cookies.get("session_token")
    user = get_user_from_token(token)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    
    friend = db.query(User).filter(User.username == friend_username).first()
    if not friend:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        
    if friend.id == user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot add yourself as a friend")

    # Check if friendship already exists
    if db.query(Friendship).filter(Friendship.user_id == user.id, Friendship.friend_id == friend.id).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Friendship already exists")

    new_friendship = Friendship(user_id=user.id, friend_id=friend.id)
    db.add(new_friendship)
    db.commit()
    
    # Broadcast to both users to update their friend lists
    await broadcast_to_all_friends()
    
    return JSONResponse({"message": f"{friend.full_name} added as a friend"})

@app.get("/search_users")
async def search_users(query: str, db: SessionLocal = Depends(get_db)):
    users = db.query(User).filter(User.username.like(f"%{query}%")).limit(10).all()
    search_results = [{
        "id": u.id, 
        "username": u.username, 
        "full_name": u.full_name, 
        "profile_pic": u.profile_pic
    } for u in users]
    return JSONResponse({"users": search_results})


# WebSocket endpoint for real-time signaling
@app.websocket("/ws/{client_id}/{token}")
async def websocket_endpoint(websocket: WebSocket, client_id: str, token: str):
    user = get_user_from_token(token)
    if not user or user.id != client_id:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
        
    await websocket.accept()
    active_connections[client_id] = websocket
    
    db = SessionLocal()
    user_db = db.query(User).filter(User.id == client_id).first()
    if user_db:
        user_db.is_online = True
        db.commit()
        db.close()
    
    print(f"Client {user.username} connected. Online status updated.")
    
    # Broadcast the updated online list to all users
    await broadcast_to_all_friends()

    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            
            print(f"Received message from {client_id}: {message}")

            target_id = message.get("target_id")
            if not target_id:
                print("Message has no target_id, ignoring.")
                continue

            target_websocket = active_connections.get(target_id)
            if target_websocket:
                message["sender_id"] = client_id
                await target_websocket.send_text(json.dumps(message))
            else:
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "message": f"User {target_id} is not online."
                }))
                print(f"User {target_id} not found.")

    except WebSocketDisconnect:
        db = SessionLocal()
        user_db = db.query(User).filter(User.id == client_id).first()
        if user_db:
            user_db.is_online = False
            db.commit()
        db.close()

        if client_id in active_connections:
            del active_connections[client_id]
        print(f"Client {user.username} disconnected. Online status updated.")
        
        # Broadcast the updated online list to all users
        await broadcast_to_all_friends()
        
    except Exception as e:
        print(f"An error occurred: {e}")
        db.close()
        if client_id in active_connections:
            del active_connections[client_id]
        await broadcast_to_all_friends()
