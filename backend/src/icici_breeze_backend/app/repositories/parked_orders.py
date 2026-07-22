"""CRUD for parked_orders in users.sqlite3."""
from __future__ import annotations

import sqlite3
import uuid
from typing import Any, Optional

from icici_breeze_backend.app.core.timezone import ist_timestamp
from icici_breeze_backend.app.domain.order import ParkedOrderItem, ParkedOrderListItem


def _db_path() -> str:
    from icici_breeze_backend.core import config as cfg

    return cfg.DATA_PATH + cfg.USERS_DB


def _row_to_item(row: sqlite3.Row) -> ParkedOrderListItem:
    d = dict(row)
    return ParkedOrderListItem(
        id=str(d["id"]),
        product_type=str(d["product_type"] or "Options"),
        stock_code=str(d["stock_code"]),
        exchange_code=str(d["exchange_code"] or "NFO"),
        expiry_date=str(d["expiry_date"]),
        right=str(d["right"]),
        strike_price=str(d["strike_price"]),
        quantity=str(d["quantity"]),
        price=str(d["price"] or "0"),
        action=d["action"],  # type: ignore[arg-type]
        chunk_qty=d.get("chunk_qty"),
        batch_group_id=d.get("batch_group_id"),
        created_at=str(d["created_at"]) if d.get("created_at") else None,
        updated_at=str(d["updated_at"]) if d.get("updated_at") else None,
    )


def list_parked_orders(user_id: str) -> list[ParkedOrderListItem]:
    db_path = _db_path()
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT id, user_id, product_type, stock_code, exchange_code, expiry_date,
                   right, strike_price, quantity, price, action, chunk_qty, batch_group_id,
                   created_at, updated_at
            FROM parked_orders
            WHERE user_id = ?
            ORDER BY created_at DESC, id DESC
            """,
            (user_id,),
        )
        return [_row_to_item(r) for r in cur.fetchall()]


def create_parked_orders(
    user_id: str,
    items: list[ParkedOrderItem],
    replace_ids: Optional[list[str]] = None,
) -> list[ParkedOrderListItem]:
    db_path = _db_path()
    replace_ids = [x.strip() for x in (replace_ids or []) if x and str(x).strip()]
    # One stamp for the whole batch: these rows are parked together, and reading back
    # different created_at values within one basket would be noise, not information.
    now = ist_timestamp()
    with sqlite3.connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if replace_ids:
                qmarks = ",".join("?" * len(replace_ids))
                conn.execute(
                    f"DELETE FROM parked_orders WHERE user_id = ? AND id IN ({qmarks})",
                    (user_id, *replace_ids),
                )
            for it in items:
                pid = str(uuid.uuid4())
                conn.execute(
                    """
                    INSERT INTO parked_orders (
                        id, user_id, product_type, stock_code, exchange_code,
                        expiry_date, right, strike_price, quantity, price, action,
                        chunk_qty, batch_group_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        pid,
                        user_id,
                        it.product_type or "Options",
                        it.stock_code.strip(),
                        (it.exchange_code or "NFO").strip(),
                        it.expiry_date.strip(),
                        it.right.strip(),
                        str(it.strike_price).strip(),
                        str(it.quantity).strip(),
                        (it.price or "0").strip(),
                        it.action,
                        it.chunk_qty.strip() if it.chunk_qty else None,
                        it.batch_group_id.strip() if it.batch_group_id else None,
                        # Bound, not left to the column DEFAULT: pre-existing databases
                        # still carry `DEFAULT CURRENT_TIMESTAMP` (UTC) and SQLite has no
                        # ALTER COLUMN to correct it.
                        now,
                        now,
                    ),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    return list_parked_orders(user_id)


def update_parked_order(
    user_id: str,
    order_id: str,
    *,
    quantity: Optional[str] = None,
    price: Optional[str] = None,
    chunk_qty: Optional[str] = None,
) -> Optional[ParkedOrderListItem]:
    """chunk_qty of '' clears stored chunk preference."""
    db_path = _db_path()
    sets: list[str] = []
    args: list[Any] = []
    if quantity is not None:
        sets.append("quantity = ?")
        args.append(str(quantity).strip())
    if price is not None:
        sets.append("price = ?")
        args.append(str(price).strip())
    if chunk_qty is not None:
        if chunk_qty == "":
            sets.append("chunk_qty = NULL")
        else:
            sets.append("chunk_qty = ?")
            args.append(chunk_qty.strip())
    if not sets:
        return None
    sets.append("updated_at = ?")
    args.append(ist_timestamp())
    args.extend([order_id.strip(), user_id])
    sql = f'UPDATE parked_orders SET {", ".join(sets)} WHERE id = ? AND user_id = ?'
    oid = order_id.strip()
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(sql, args)
        conn.commit()
        if cur.rowcount == 0:
            return None
        cur2 = conn.execute(
            """
 SELECT id, user_id, product_type, stock_code, exchange_code, expiry_date,
        right, strike_price, quantity, price, action, chunk_qty, batch_group_id,
        created_at, updated_at
 FROM parked_orders WHERE id = ? AND user_id = ?
            """,
            (oid, user_id),
        )
        row = cur2.fetchone()
        if not row:
            return None
        return _row_to_item(row)


def delete_parked_orders(user_id: str, ids: list[str]) -> int:
    if not ids:
        return 0
    clean = [i.strip() for i in ids if i and str(i).strip()]
    if not clean:
        return 0
    db_path = _db_path()
    qmarks = ",".join("?" * len(clean))
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            f"DELETE FROM parked_orders WHERE user_id = ? AND id IN ({qmarks})",
            (user_id, *clean),
        )
        conn.commit()
        return int(cur.rowcount or 0)


def delete_parked_order(user_id: str, order_id: str) -> bool:
    n = delete_parked_orders(user_id, [order_id])
    return n > 0
