"""Products and bundles.

A bundle is a fixed set of products sold at one special price. Selling a
bundle draws down each component from the flight's OBS inventory.
"""

import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from database import get_connection
from security import current_user, log_action, now_iso, requires

router = APIRouter(tags=["catalog"])


class ProductIn(BaseModel):
    product_code: str
    product_name: str
    category: str
    unit: str = "pcs"
    selling_price: float = Field(ge=0)
    cost: float = Field(ge=0)
    reorder_level: int = Field(ge=0, default=20)
    active: bool = True


class BundleItemIn(BaseModel):
    product_id: int
    quantity: int = Field(gt=0)


class BundleIn(BaseModel):
    bundle_code: str
    bundle_name: str
    bundle_price: float = Field(ge=0)
    items: list[BundleItemIn]
    active: bool = True


# ------------------------------- products --------------------------------- #
@router.get("/products")
def list_products(include_inactive: bool = False, user: dict = Depends(current_user)):
    connection = get_connection()
    try:
        sql = """SELECT p.*, COALESCE(m.quantity,0) AS main_stock
                 FROM products p LEFT JOIN main_inventory m ON m.product_id = p.id"""
        if not include_inactive:
            sql += " WHERE p.active=1"
        sql += " ORDER BY p.category, p.product_name"
        return [dict(r) for r in connection.execute(sql).fetchall()]
    finally:
        connection.close()


@router.post("/products")
def create_product(data: ProductIn, user: dict = Depends(requires("inventory"))):
    code = data.product_code.strip().upper()
    connection = get_connection()
    try:
        cur = connection.execute(
            """INSERT INTO products
               (product_code,product_name,category,unit,selling_price,cost,reorder_level,active)
               VALUES (?,?,?,?,?,?,?,?)""",
            (code, data.product_name.strip(), data.category.strip(), data.unit.strip(),
             data.selling_price, data.cost, data.reorder_level, int(data.active)),
        )
        product_id = cur.lastrowid
        connection.execute(
            "INSERT OR IGNORE INTO main_inventory(product_id,quantity,updated_at) VALUES (?,0,?)",
            (product_id, now_iso()),
        )
        connection.commit()
        row = connection.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="That product code already exists.")
    finally:
        connection.close()
    log_action(user, "PRODUCT", "CREATE_PRODUCT", f"Created product {code} - {data.product_name}.",
               "product", product_id)
    return dict(row)


@router.put("/products/{product_id}")
def update_product(product_id: int, data: ProductIn, user: dict = Depends(requires("inventory"))):
    connection = get_connection()
    try:
        if not connection.execute("SELECT 1 FROM products WHERE id=?", (product_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Product not found.")
        connection.execute(
            """UPDATE products SET product_code=?, product_name=?, category=?, unit=?,
                   selling_price=?, cost=?, reorder_level=?, active=? WHERE id=?""",
            (data.product_code.strip().upper(), data.product_name.strip(), data.category.strip(),
             data.unit.strip(), data.selling_price, data.cost, data.reorder_level,
             int(data.active), product_id),
        )
        connection.commit()
        row = connection.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="That product code already exists.")
    finally:
        connection.close()
    log_action(user, "PRODUCT", "UPDATE_PRODUCT", f"Updated product {data.product_code}.",
               "product", product_id)
    return dict(row)


@router.delete("/products/{product_id}")
def deactivate_product(product_id: int, user: dict = Depends(requires("inventory"))):
    connection = get_connection()
    try:
        row = connection.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Product not found.")
        connection.execute("UPDATE products SET active=0 WHERE id=?", (product_id,))
        connection.execute("UPDATE bundle_items SET quantity=quantity WHERE product_id=?", (product_id,))
        connection.commit()
    finally:
        connection.close()
    log_action(user, "PRODUCT", "DEACTIVATE_PRODUCT", f"Deactivated product {row['product_code']}.",
               "product", product_id)
    return {"status": "success"}


# -------------------------------- bundles --------------------------------- #
def _bundle(connection, bundle_id):
    row = connection.execute("SELECT * FROM bundles WHERE id=?", (bundle_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Bundle not found.")
    bundle = dict(row)
    bundle["items"] = [dict(r) for r in connection.execute(
        """SELECT bi.product_id, bi.quantity, p.product_code, p.product_name,
                  p.category, p.selling_price
           FROM bundle_items bi JOIN products p ON p.id = bi.product_id
           WHERE bi.bundle_id=? ORDER BY p.product_name""",
        (bundle_id,),
    ).fetchall()]
    bundle["component_value"] = round(
        sum(i["quantity"] * float(i["selling_price"]) for i in bundle["items"]), 2
    )
    bundle["saving"] = round(bundle["component_value"] - float(bundle["bundle_price"]), 2)
    return bundle


@router.get("/bundles")
def list_bundles(include_inactive: bool = False, user: dict = Depends(current_user)):
    connection = get_connection()
    try:
        sql = "SELECT id FROM bundles"
        if not include_inactive:
            sql += " WHERE active=1"
        sql += " ORDER BY bundle_name"
        return [_bundle(connection, r["id"]) for r in connection.execute(sql).fetchall()]
    finally:
        connection.close()


@router.post("/bundles")
def create_bundle(data: BundleIn, user: dict = Depends(requires("bundles"))):
    return _write_bundle(None, data, user)


@router.put("/bundles/{bundle_id}")
def update_bundle(bundle_id: int, data: BundleIn, user: dict = Depends(requires("bundles"))):
    return _write_bundle(bundle_id, data, user)


def _write_bundle(bundle_id, data: BundleIn, user):
    if not data.items:
        raise HTTPException(status_code=400, detail="A bundle needs at least one product.")
    if len({i.product_id for i in data.items}) != len(data.items):
        raise HTTPException(status_code=400, detail="The same product cannot appear twice in a bundle.")

    code = data.bundle_code.strip().upper()
    connection = get_connection()
    try:
        for item in data.items:
            ok = connection.execute(
                "SELECT 1 FROM products WHERE id=? AND active=1", (item.product_id,)
            ).fetchone()
            if not ok:
                raise HTTPException(status_code=400, detail="A selected product is not active.")

        if bundle_id is None:
            cur = connection.execute(
                """INSERT INTO bundles(bundle_code,bundle_name,bundle_price,active,created_by,created_at)
                   VALUES (?,?,?,?,?,?)""",
                (code, data.bundle_name.strip(), data.bundle_price, int(data.active),
                 user["employee_id"], now_iso()),
            )
            bundle_id = cur.lastrowid
            action = "CREATE_BUNDLE"
        else:
            if not connection.execute("SELECT 1 FROM bundles WHERE id=?", (bundle_id,)).fetchone():
                raise HTTPException(status_code=404, detail="Bundle not found.")
            connection.execute(
                "UPDATE bundles SET bundle_code=?, bundle_name=?, bundle_price=?, active=? WHERE id=?",
                (code, data.bundle_name.strip(), data.bundle_price, int(data.active), bundle_id),
            )
            connection.execute("DELETE FROM bundle_items WHERE bundle_id=?", (bundle_id,))
            action = "UPDATE_BUNDLE"

        for item in data.items:
            connection.execute(
                "INSERT INTO bundle_items(bundle_id,product_id,quantity) VALUES (?,?,?)",
                (bundle_id, item.product_id, item.quantity),
            )
        connection.commit()
        result = _bundle(connection, bundle_id)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="That bundle code already exists.")
    finally:
        connection.close()

    log_action(user, "BUNDLE", action,
               f"{action.replace('_', ' ').title()}: {code} at NPR {data.bundle_price:,.2f}.",
               "bundle", bundle_id, metadata={"items": [i.model_dump() for i in data.items]})
    return result


@router.delete("/bundles/{bundle_id}")
def deactivate_bundle(bundle_id: int, user: dict = Depends(requires("bundles"))):
    connection = get_connection()
    try:
        row = connection.execute("SELECT * FROM bundles WHERE id=?", (bundle_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Bundle not found.")
        connection.execute("UPDATE bundles SET active=0 WHERE id=?", (bundle_id,))
        connection.commit()
    finally:
        connection.close()
    log_action(user, "BUNDLE", "DEACTIVATE_BUNDLE", f"Deactivated bundle {row['bundle_code']}.",
               "bundle", bundle_id)
    return {"status": "success"}
