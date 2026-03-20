from typing import List
from fastapi import APIRouter
from fastapi import Request, Depends
from fastapi import responses
from fastapi import status
from fastapi.templating import Jinja2Templates
from app.auth.context import get_request_context_or_redirect, RequestContext
from app.domain.order import BookActionRequest
from app.services.processor import processor
from app.api.error_utils import render_error_page
from app.api.v1.route_admin import get_common_template_vars
import app.core.config as cfg
import datetime


async def get_book_form_request(request: Request) -> BookActionRequest:
    """Read form directly so order_ids is always a list (one or many checkboxes)."""
    form = await request.form()
    order_ids = form.getlist("order_ids")
    ids = [x for x in order_ids if isinstance(x, str) and x.strip()]
    return BookActionRequest(
        order_ids=ids,
        start=form.get("start"),
        end=form.get("end"),
        action=form.get("action") or "",
    )

templates = Jinja2Templates(directory="templates")
router = APIRouter()
breeze = processor()


@router.get("")
async def serve_landing(
    request: Request,
    context: RequestContext = Depends(get_request_context_or_redirect),
    start: str | None = None,
    end: str | None = None,
):
    user_id = context.user_id
    error = {}

    customer = breeze.get_customer_details(user_id)
    if customer is None:
        error['location'] = "In route_book.py --> serve_landing() get_customer_details returned None"
        error['contents'] = "get_customer_details() returned None for user_id = " + user_id
        breeze.store_error(error)
    elif customer['Status'] != 200:
        error['location'] = "In route_book.py --> serve_landing() get_customer_details failed"
        error['contents'] = "customer['status'] = " + str(customer['Status']) + " and customer['error'] = " + customer['Error']
        breeze.store_error(error)

    margin = breeze.get_margin_situation(user_id, target_margin_ute=100)
    if margin['Status'] != 200:
        error['location'] = "In route_book.py --> serve_landing() get_margin_situation failed"
        error['contents'] = "margin['status'] = " + str(margin['Status']) + " and margin['error'] = " + margin['Error']
        breeze.store_error(error)

    messages = breeze.retrieve_messages(user_id)

    if start is None or end is None:
        start = datetime.datetime.today().strftime("%Y-%m-%d")
        start_date = datetime.datetime.strptime(start, "%Y-%m-%d")
        next_day = start_date
        while True:
            next_day += datetime.timedelta(days=1)
            if next_day.weekday() < 5:
                break
        end = next_day.strftime("%Y-%m-%d")

    orders = breeze.get_orders(user_id, start=start, end=end)
    orders_failed = False
    if orders['Status'] != 200:
        grouped_orders = None
        orders_failed = True
    else:
        if orders['Success'] is None:
            grouped_orders = None
        else:
            grouped_orders = breeze.group_orders(user_id, orders)

    if orders_failed:
        messages = list(messages) if messages else []
        messages.append({"type": cfg.WARNING, "message": "Orders could not be loaded for the selected date range. Try a shorter range or try again."})

    errors = breeze.retrieve_errors()

    if len(errors) == 0:
        return templates.TemplateResponse("book.html", {"request": request, "is_logged_in": True, "login_url": None, "active": "book", "customer": customer, "margin": margin, "messages": messages, "grouped_orders": grouped_orders, "start": start, "end": end, **get_common_template_vars(context)})
    else:
        return render_error_page(request, errors, active="book", log_context="route_book.serve_landing")


@router.post("")
async def process_post(
    request: Request,
    context: RequestContext = Depends(get_request_context_or_redirect),
    form: BookActionRequest = Depends(get_book_form_request),
):
    user_id = context.user_id

    if form.action == cfg.CANCEL and form.order_ids:
        messages = breeze.cancel_orders(user_id, form.order_ids)
        breeze.store_messages(user_id, messages)
        return responses.RedirectResponse("/book", status_code=status.HTTP_302_FOUND)
    return responses.RedirectResponse(url=f"/book?start={form.start or ''}&end={form.end or ''}", status_code=status.HTTP_302_FOUND)
