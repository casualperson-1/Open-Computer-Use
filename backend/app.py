import os
import json
import asyncio
from typing import List, Optional
from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect, status, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select

from .config import settings
from .security.crypto import vault
from .database.models import Base, User, ApiKey, ScheduledTask, TaskRun, DealRecord
from .agent.skills.deal_finder import DealFinderEngine, Deal
from .scheduler.manager import TaskSchedulerManager

app = FastAPI(title="Self-Hosted Browser Agent")

# In-memory connection manager for WebSocket step streaming
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                pass

ws_manager = ConnectionManager()
deal_engine = DealFinderEngine()

DB_PATH = "/tmp/agent.db"
engine = create_async_engine(f"sqlite+aiosqlite:///{DB_PATH}", echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
scheduler_mgr = TaskSchedulerManager(db_url=f"sqlite+aiosqlite:///{DB_PATH}")

@app.on_event("startup")
async def startup_event():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    scheduler_mgr.start()

@app.on_event("shutdown")
def shutdown_event():
    scheduler_mgr.shutdown()

# Models
class KeyUpdate(BaseModel):
    provider: str
    key: str

class TaskCreate(BaseModel):
    name: str
    goal: str
    interval_seconds: int = 120

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    static_file = os.path.join(os.path.dirname(__file__), "static", "index.html")
    if os.path.exists(static_file):
        with open(static_file, "r") as f:
            return f.read()
    return "<h1>Browser Agent API is active</h1>"

@app.post("/api/keys")
async def save_api_key(item: KeyUpdate):
    encrypted = vault.encrypt(item.key)
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(ApiKey).filter(ApiKey.provider == item.provider))
        existing = result.scalars().first()
        if existing:
            existing.encrypted_key = encrypted
        else:
            session.add(ApiKey(provider=item.provider, encrypted_key=encrypted))
        await session.commit()
    return {"status": "saved", "provider": item.provider}

@app.get("/api/tasks")
async def list_tasks():
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(ScheduledTask))
        tasks = result.scalars().all()
        return [{"id": t.id, "name": t.name, "goal": t.goal, "active": t.is_active} for t in tasks]

@app.post("/api/tasks")
async def create_task(t: TaskCreate):
    task_id = await scheduler_mgr.schedule_task(t.name, t.goal, t.interval_seconds)
    return {"status": "scheduled", "id": task_id}

@app.get("/api/deals")
async def list_deals():
    # Return in-memory or persisted deals
    all_deals = []
    for d_list in deal_engine.price_history.values():
        all_deals.extend([d.__dict__ for d in d_list])
    return all_deals

@app.get("/api/deals/export")
async def export_deals():
    all_deals = []
    for d_list in deal_engine.price_history.values():
        all_deals.extend([d.__dict__ for d in d_list])
    csv_data = deal_engine.export_csv(all_deals)
    return PlainTextResponse(content=csv_data, media_type="text/csv")

@app.websocket("/ws/events")
async def websocket_events(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            # Handle client pause / stop / resume requests
            msg = json.loads(data)
            await ws_manager.broadcast({"type": "client_event", "data": msg})
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
