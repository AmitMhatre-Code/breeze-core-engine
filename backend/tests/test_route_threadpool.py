"""B-08: handlers that do broker work run on the threadpool, not the event loop that also
runs PB/SL evaluation, the tick flush and the watchdogs."""
from __future__ import annotations

import inspect
import threading
import time

from icici_breeze_backend.app.api.v1 import (
    home,
    route_book,
    route_bots,
    route_dashboard,
    route_order,
    route_portfolio,
)


def test_broker_work_handlers_are_not_coroutines():
    for module in (route_portfolio, route_dashboard, route_book, route_order, route_bots):
        for route in module.router.routes:
            assert not inspect.iscoroutinefunction(route.endpoint), f"{module.__name__}.{route.name}"
    assert not inspect.iscoroutinefunction(home.get_home_api)
    assert not inspect.iscoroutinefunction(home.updatemaster)


def test_one_placement_at_a_time_per_user():
    """The idempotency check is only safe when a retry cannot run beside the first send."""
    active = {"now": 0, "max": 0}
    guard = threading.Lock()

    @route_order._one_placement_at_a_time
    def place(*, context):
        with guard:
            active["now"] += 1
            active["max"] = max(active["max"], active["now"])
        time.sleep(0.05)
        with guard:
            active["now"] -= 1

    class Ctx:
        user_id = "u1"

    threads = [threading.Thread(target=place, kwargs={"context": Ctx()}) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert active["max"] == 1


def test_errors_are_kept_per_thread():
    from icici_breeze_backend.app.services.processor import processor

    p = processor()
    p.retrieve_errors()
    p.store_error({"contents": "main"})
    seen = {}

    def other():
        seen["other"] = p.retrieve_errors()

    t = threading.Thread(target=other)
    t.start()
    t.join()
    assert seen["other"] == []
    assert p.retrieve_errors() == [{"contents": "main"}]
