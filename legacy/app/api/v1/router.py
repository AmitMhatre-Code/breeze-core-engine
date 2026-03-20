"""Aggregates all v1 API routers."""
from fastapi import APIRouter

from app.api.v1 import auth
from app.api.v1 import home
from app.api.v1 import route_portfolio
from app.api.v1 import route_order
from app.api.v1 import route_book
from app.api.v1 import route_uncovered_shorts
from app.api.v1 import route_vertical_spread
from app.api.v1 import route_hedge
from app.api.v1 import route_performance
from app.api.v1 import route_audit
from app.api.v1 import route_admin
from app.api.v1 import route_settings
from app.api.v1 import route_google_auth
from app.api.v1 import route_register
from app.api.v1 import route_dashboard

v1_router = APIRouter()

v1_router.include_router(route_google_auth.router, prefix="", include_in_schema=False)
v1_router.include_router(route_register.router, prefix="", include_in_schema=False)
v1_router.include_router(auth.router, prefix="", tags=["auth"], include_in_schema=True)
v1_router.include_router(home.router, prefix="", tags=[""], include_in_schema=False)
v1_router.include_router(route_portfolio.router, prefix="/portfolio", tags=[""], include_in_schema=False)
v1_router.include_router(route_order.router, prefix="/order", tags=[""], include_in_schema=False)
v1_router.include_router(route_book.router, prefix="/book", tags=[""], include_in_schema=False)
v1_router.include_router(route_uncovered_shorts.router, prefix="/uncovered-shorts", tags=[""], include_in_schema=False)
v1_router.include_router(route_vertical_spread.router, prefix="/vertical-spread", tags=[""], include_in_schema=False)
v1_router.include_router(route_hedge.router, prefix="/hedge", tags=[""], include_in_schema=False)
v1_router.include_router(route_performance.router, prefix="/performance", tags=[""], include_in_schema=False)
v1_router.include_router(route_audit.router, prefix="/audit", tags=["audit"], include_in_schema=True)
v1_router.include_router(route_admin.router, prefix="/admin", tags=["admin"], include_in_schema=False)
v1_router.include_router(route_settings.router, prefix="/settings", tags=[""], include_in_schema=False)
v1_router.include_router(route_dashboard.router, prefix="/dashboard", tags=["dashboard"], include_in_schema=False)
