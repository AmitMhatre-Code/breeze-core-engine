"""Main API router - includes v1 routes."""
from fastapi import APIRouter

from app.api.v1.router import v1_router

app_router = APIRouter()
app_router.include_router(v1_router)
