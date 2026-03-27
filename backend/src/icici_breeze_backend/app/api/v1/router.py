"""Aggregates all v1 API routers."""
from fastapi import APIRouter

from icici_breeze_backend.app.api.v1 import auth
from icici_breeze_backend.app.api.v1 import home
from icici_breeze_backend.app.api.v1 import route_portfolio
from icici_breeze_backend.app.api.v1 import route_order
from icici_breeze_backend.app.api.v1 import route_uncovered_shorts
from icici_breeze_backend.app.api.v1 import route_vertical_spread
from icici_breeze_backend.app.api.v1 import route_hedge
from icici_breeze_backend.app.api.v1 import route_settings
from icici_breeze_backend.app.api.v1 import route_google_auth
from icici_breeze_backend.app.api.v1 import route_dashboard
from icici_breeze_backend.app.api.v1 import route_outlook
from icici_breeze_backend.app.api.v1 import route_register
from icici_breeze_backend.app.api.v1 import route_book
from icici_breeze_backend.app.api.v1 import route_performance
from icici_breeze_backend.app.api.v1 import route_admin
from icici_breeze_backend.app.api.v1 import route_strategy_builder

v1_router = APIRouter()

v1_router.include_router(route_google_auth.router, prefix="", include_in_schema=False)
v1_router.include_router(auth.router, prefix="", tags=["auth"], include_in_schema=True)
v1_router.include_router(route_register.router, prefix="", include_in_schema=False)
v1_router.include_router(home.router, prefix="", tags=[""], include_in_schema=False)
v1_router.include_router(route_portfolio.router, prefix="/portfolio", tags=[""], include_in_schema=False)
v1_router.include_router(route_order.router, prefix="/order", tags=[""], include_in_schema=False)
v1_router.include_router(route_book.router, prefix="/book", tags=[""], include_in_schema=False)
v1_router.include_router(route_uncovered_shorts.router, prefix="/uncovered-shorts", tags=[""], include_in_schema=False)
v1_router.include_router(route_vertical_spread.router, prefix="/vertical-spread", tags=[""], include_in_schema=False)
v1_router.include_router(route_hedge.router, prefix="/hedge", tags=[""], include_in_schema=False)
v1_router.include_router(route_settings.router, prefix="", include_in_schema=False)
v1_router.include_router(route_dashboard.router, prefix="/dashboard", tags=["dashboard"], include_in_schema=False)
v1_router.include_router(route_outlook.router, prefix="", tags=["outlook"], include_in_schema=False)
v1_router.include_router(route_performance.router, prefix="/performance", tags=[""], include_in_schema=False)
v1_router.include_router(route_admin.router, prefix="/admin", tags=[""], include_in_schema=False)
v1_router.include_router(
    route_strategy_builder.router,
    prefix="/strategy-builder",
    tags=[""],
    include_in_schema=False,
)
