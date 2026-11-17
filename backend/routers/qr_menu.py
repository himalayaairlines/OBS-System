"""QR Menu: the passenger menu opened by scanning a QR code.

Only the administrator (permission `qr_menu`) can see and change it in OBS:
sections, items (bilingual names, description, price, photo), menu posters and
the page texts. Passengers open the read-only page at /menu, with no sign-in
and nothing else reachable from it.

For flights without internet, the administrator downloads an offline package:
the same page with its photos plus the small Python server and QR generator,
to run on the onboard laptop exactly like the previous Koseli menu.
"""

import io
import json
import secrets
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel, Field

import qrgen
from database import DB_PATH, get_connection
from menu_page import render_menu
from security import log_action, requires

router = APIRouter(tags=["qr menu"])

MEDIA_DIR = Path(DB_PATH).resolve().parent / "menu_media"
OFFLINE_DIR = Path(__file__).resolve().parents[1] / "menu_offline"
MAX_IMAGE_BYTES = 6 * 1024 * 1024
IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS qr_menu_sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name_en TEXT NOT NULL, name_zh TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
    updated_by TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS qr_menu_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    section_id INTEGER NOT NULL REFERENCES qr_menu_sections(id),
    name_en TEXT NOT NULL, name_zh TEXT, desc_en TEXT, desc_zh TEXT,
    price REAL, photo TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
    updated_by TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS qr_menu_posters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT, image TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
    updated_by TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS qr_menu_settings (
    key TEXT PRIMARY KEY, value TEXT
);
"""

DEFAULT_SETTINGS = {
    "title_en": "Koseli Onboard Menu", "title_zh": "机上精选菜单",
    "subtitle_en": "Himalaya Airlines", "subtitle_zh": "喜马拉雅航空",
    "note_en": "Explore the Koseli On board sales menu, pre-purchase your favorites at the sales counter, present your receipt onboard, and enjoy your journey with Himalaya Airlines.",
    "note_zh": "浏览 Koseli 机上销售菜单，在销售柜台预购您喜爱的商品，登机后出示收据，与喜马拉雅航空一起享受旅程。",
    "footer_en": "Prices are in Nepalese Rupees. Other currencies accepted at the day's rate.",
    "footer_zh": "价格以尼泊尔卢比计。可按当日汇率使用其他货币支付。",
    "currency": "NPR", "logo": "", "banner": "",
}


def initialize_qr_menu(connection):
    connection.executescript(SCHEMA)
    for key, value in DEFAULT_SETTINGS.items():
        connection.execute("INSERT OR IGNORE INTO qr_menu_settings(key,value) VALUES (?,?)", (key, value))
    # The old "tap an item" wording no longer fits: purchases are made at the
    # sales counter, and the page explains that in "How Koseli Works".
    for key, stale in (("note_en", ("Tap an item",)), ("note_zh", ("点击商品",))):
        row = connection.execute("SELECT value FROM qr_menu_settings WHERE key=?", (key,)).fetchone()
        if row and any(row["value"].startswith(x) for x in stale):
            connection.execute("UPDATE qr_menu_settings SET value=? WHERE key=?", (DEFAULT_SETTINGS[key], key))
    connection.commit()
    # First start: load the printed Koseli menu so the QR page is never empty.
    from menu_seed import load_koseli_menu, menu_is_empty
    if menu_is_empty(connection) and not connection.execute(
            "SELECT 1 FROM qr_menu_settings WHERE key='seeded'").fetchone():
        load_koseli_menu(connection, MEDIA_DIR)
        connection.execute("INSERT OR REPLACE INTO qr_menu_settings(key,value) VALUES ('seeded','1')")


def _now():
    return datetime.now().replace(microsecond=0).isoformat()


# --------------------------------------------------------------------------- #
# Reading the menu
# --------------------------------------------------------------------------- #
def _settings(connection) -> dict:
    out = dict(DEFAULT_SETTINGS)
    out.update({r["key"]: r["value"] for r in connection.execute("SELECT key, value FROM qr_menu_settings")})
    return out


def menu_data(connection, include_hidden=False) -> dict:
    active = "" if include_hidden else "WHERE active=1"
    sections = [dict(r) for r in connection.execute(
        f"SELECT * FROM qr_menu_sections {active} ORDER BY sort_order, id").fetchall()]
    items = [dict(r) for r in connection.execute(
        f"SELECT * FROM qr_menu_items {active} ORDER BY sort_order, id").fetchall()]
    posters = [dict(r) for r in connection.execute(
        f"SELECT * FROM qr_menu_posters {active} ORDER BY sort_order, id").fetchall()]
    for s in sections:
        s["items"] = [i for i in items if i["section_id"] == s["id"]]
    return {"settings": _settings(connection), "sections": sections, "posters": posters,
            "unsectioned": [i for i in items if i["section_id"] not in {s["id"] for s in sections}]}


def _public_data(connection) -> dict:
    data = menu_data(connection)
    keep_item = ("id", "name_en", "name_zh", "desc_en", "desc_zh", "price", "photo")
    return {
        "settings": data["settings"],
        "sections": [{"id": s["id"], "name_en": s["name_en"], "name_zh": s["name_zh"],
                      "items": [{k: i[k] for k in keep_item} for i in s["items"]]} for s in data["sections"]],
        "posters": [{"title": p["title"], "image": p["image"]} for p in data["posters"]],
    }


def _logo_html(settings, media_base):
    if settings.get("logo"):
        return f'<img src="{media_base}{settings["logo"]}" alt="">'
    return '<b style="color:#D62E2E;font-size:17px">H9</b>'


@router.get("/public/menu", response_class=HTMLResponse)
def public_menu_page():
    """The passenger page. No sign-in. Served at /menu by the web server."""
    connection = get_connection()
    try:
        data = _public_data(connection)
    finally:
        connection.close()
    html = render_menu(data, "/menu/media/", _logo_html(data["settings"], "/menu/media/"), "/menu/koseli-menu.pdf")
    return HTMLResponse(html, headers={"Cache-Control": "no-cache", "X-Robots-Tag": "noindex",
                                       "Referrer-Policy": "no-referrer"})


@router.get("/public/menu/koseli-menu.pdf")
def public_menu_pdf():
    """The whole menu as one PDF (posters, then every item and price) for the
    passenger to keep on the phone during the flight."""
    connection = get_connection()
    try:
        data = _public_data(connection)
    finally:
        connection.close()
    from menu_pdf import build_menu_pdf
    return Response(build_menu_pdf(data, MEDIA_DIR), media_type="application/pdf",
                    headers={"Content-Disposition": 'inline; filename="koseli-menu.pdf"', "Cache-Control": "no-cache"})


@router.get("/public/menu/media/{name}")
def public_menu_media(name: str):
    if "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(status_code=404, detail="Not found.")
    path = MEDIA_DIR / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Not found.")
    return FileResponse(path, headers={"Cache-Control": "public, max-age=86400"})


# --------------------------------------------------------------------------- #
# Administration (admin only)
# --------------------------------------------------------------------------- #
class SectionIn(BaseModel):
    name_en: str = Field(min_length=1, max_length=80)
    name_zh: str | None = Field(default=None, max_length=80)
    sort_order: int = 0
    active: bool = True


class ItemIn(BaseModel):
    section_id: int
    name_en: str = Field(min_length=1, max_length=160)
    name_zh: str | None = Field(default=None, max_length=160)
    desc_en: str | None = Field(default=None, max_length=500)
    desc_zh: str | None = Field(default=None, max_length=500)
    price: float | None = Field(default=None, ge=0)
    photo: str | None = Field(default=None, max_length=120)
    sort_order: int = 0
    active: bool = True


class PosterIn(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    image: str = Field(min_length=1, max_length=120)
    sort_order: int = 0
    active: bool = True


class SettingsIn(BaseModel):
    title_en: str = Field(min_length=1, max_length=120)
    title_zh: str | None = Field(default=None, max_length=120)
    subtitle_en: str | None = Field(default=None, max_length=160)
    subtitle_zh: str | None = Field(default=None, max_length=160)
    note_en: str | None = Field(default=None, max_length=400)
    note_zh: str | None = Field(default=None, max_length=400)
    footer_en: str | None = Field(default=None, max_length=1500)
    footer_zh: str | None = Field(default=None, max_length=1500)
    currency: str = Field(default="NPR", min_length=1, max_length=8)
    logo: str | None = Field(default=None, max_length=120)
    banner: str | None = Field(default=None, max_length=120)


def _check_image(name):
    if name and not (MEDIA_DIR / name).is_file():
        raise HTTPException(status_code=400, detail="That picture was not found. Upload it again.")


@router.get("/qr-menu")
def admin_menu(user: dict = Depends(requires("qr_menu"))):
    connection = get_connection()
    try:
        return menu_data(connection, include_hidden=True)
    finally:
        connection.close()


@router.post("/qr-menu/images")
async def upload_image(file: UploadFile = File(...), user: dict = Depends(requires("qr_menu"))):
    ext = IMAGE_TYPES.get((file.content_type or "").lower())
    if not ext:
        raise HTTPException(status_code=400, detail="Upload a JPG, PNG or WebP picture.")
    body = await file.read()
    if len(body) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail="The picture is larger than 6 MB. Use a smaller one.")
    signatures = {".jpg": (b"\xff\xd8",), ".png": (b"\x89PNG",), ".webp": (b"RIFF",)}
    if not body.startswith(signatures[ext]):
        raise HTTPException(status_code=400, detail="That file is not a valid picture.")
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{secrets.token_hex(8)}{ext}"
    (MEDIA_DIR / name).write_bytes(body)
    return {"name": name, "url": f"/menu/media/{name}"}


def _save(table, row_id, values, user, action, label):
    connection = get_connection()
    try:
        values = {**values, "updated_by": user["employee_id"], "updated_at": _now()}
        if row_id is None:
            cols = ",".join(values)
            row_id = connection.execute(
                f"INSERT INTO {table}({cols}) VALUES ({','.join('?' * len(values))})",
                tuple(values.values())).lastrowid
        else:
            if not connection.execute(f"SELECT 1 FROM {table} WHERE id=?", (row_id,)).fetchone():
                raise HTTPException(status_code=404, detail="Not found.")
            connection.execute(f"UPDATE {table} SET {','.join(f'{k}=?' for k in values)} WHERE id=?",
                               (*values.values(), row_id))
        connection.commit()
        row = dict(connection.execute(f"SELECT * FROM {table} WHERE id=?", (row_id,)).fetchone())
    finally:
        connection.close()
    log_action(user, "QR MENU", action, f"{label}.", table, row_id)
    return row


@router.post("/qr-menu/sections")
def create_section(data: SectionIn, user: dict = Depends(requires("qr_menu"))):
    return _save("qr_menu_sections", None, {**data.model_dump(), "active": int(data.active)}, user,
                 "CREATE_SECTION", f"Added menu section {data.name_en}")


@router.put("/qr-menu/sections/{section_id}")
def update_section(section_id: int, data: SectionIn, user: dict = Depends(requires("qr_menu"))):
    return _save("qr_menu_sections", section_id, {**data.model_dump(), "active": int(data.active)}, user,
                 "UPDATE_SECTION", f"Changed menu section {data.name_en}")


@router.delete("/qr-menu/sections/{section_id}")
def delete_section(section_id: int, user: dict = Depends(requires("qr_menu"))):
    connection = get_connection()
    try:
        row = connection.execute("SELECT * FROM qr_menu_sections WHERE id=?", (section_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Not found.")
        count = connection.execute("SELECT COUNT(*) c FROM qr_menu_items WHERE section_id=?",
                                   (section_id,)).fetchone()["c"]
        if count:
            raise HTTPException(status_code=400,
                                detail=f"This section still has {count} item(s). Move or delete them first.")
        connection.execute("DELETE FROM qr_menu_sections WHERE id=?", (section_id,))
        connection.commit()
    finally:
        connection.close()
    log_action(user, "QR MENU", "DELETE_SECTION", f"Deleted menu section {row['name_en']}.",
               "qr_menu_sections", section_id)
    return {"status": "deleted"}


def _item_values(data: ItemIn):
    connection = get_connection()
    try:
        if not connection.execute("SELECT 1 FROM qr_menu_sections WHERE id=?", (data.section_id,)).fetchone():
            raise HTTPException(status_code=400, detail="Choose a menu section.")
    finally:
        connection.close()
    _check_image(data.photo)
    values = data.model_dump()
    values["active"] = int(data.active)
    return values


@router.post("/qr-menu/items")
def create_item(data: ItemIn, user: dict = Depends(requires("qr_menu"))):
    return _save("qr_menu_items", None, _item_values(data), user, "CREATE_ITEM",
                 f"Added menu item {data.name_en} at {data.price}")


@router.put("/qr-menu/items/{item_id}")
def update_item(item_id: int, data: ItemIn, user: dict = Depends(requires("qr_menu"))):
    return _save("qr_menu_items", item_id, _item_values(data), user, "UPDATE_ITEM",
                 f"Changed menu item {data.name_en} (price {data.price})")


@router.delete("/qr-menu/items/{item_id}")
def delete_item(item_id: int, user: dict = Depends(requires("qr_menu"))):
    return _delete("qr_menu_items", item_id, user, "DELETE_ITEM", "name_en")


@router.post("/qr-menu/posters")
def create_poster(data: PosterIn, user: dict = Depends(requires("qr_menu"))):
    _check_image(data.image)
    return _save("qr_menu_posters", None, {**data.model_dump(), "active": int(data.active)}, user,
                 "CREATE_POSTER", f"Added menu poster {data.title or data.image}")


@router.put("/qr-menu/posters/{poster_id}")
def update_poster(poster_id: int, data: PosterIn, user: dict = Depends(requires("qr_menu"))):
    _check_image(data.image)
    return _save("qr_menu_posters", poster_id, {**data.model_dump(), "active": int(data.active)}, user,
                 "UPDATE_POSTER", f"Changed menu poster {data.title or data.image}")


@router.delete("/qr-menu/posters/{poster_id}")
def delete_poster(poster_id: int, user: dict = Depends(requires("qr_menu"))):
    return _delete("qr_menu_posters", poster_id, user, "DELETE_POSTER", "title")


def _delete(table, row_id, user, action, label_col):
    connection = get_connection()
    try:
        row = connection.execute(f"SELECT * FROM {table} WHERE id=?", (row_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Not found.")
        connection.execute(f"DELETE FROM {table} WHERE id=?", (row_id,))
        connection.commit()
    finally:
        connection.close()
    log_action(user, "QR MENU", action, f"Deleted {row[label_col] or 'entry'} from the QR menu.", table, row_id)
    return {"status": "deleted"}


@router.put("/qr-menu/settings")
def update_settings(data: SettingsIn, user: dict = Depends(requires("qr_menu"))):
    _check_image(data.logo)
    _check_image(data.banner)
    connection = get_connection()
    try:
        for key, value in data.model_dump().items():
            connection.execute("INSERT INTO qr_menu_settings(key,value) VALUES (?,?) "
                               "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value or ""))
        connection.commit()
        settings = _settings(connection)
    finally:
        connection.close()
    log_action(user, "QR MENU", "UPDATE_SETTINGS", "Changed the QR menu page texts.", "qr_menu_settings", None)
    return settings


@router.post("/qr-menu/restore-koseli")
def restore_koseli(user: dict = Depends(requires("qr_menu"))):
    """Throw away every change and reload the printed Koseli menu."""
    from menu_seed import load_koseli_menu
    connection = get_connection()
    try:
        result = load_koseli_menu(connection, MEDIA_DIR, by=user["employee_id"])
        connection.execute("INSERT OR REPLACE INTO qr_menu_settings(key,value) VALUES ('seeded','1')")
        connection.commit()
    finally:
        connection.close()
    log_action(user, "QR MENU", "RESTORE_DEFAULT",
               f"Restored the printed Koseli menu ({result['items']} items, {result['posters']} posters).", None, None)
    return result


@router.get("/qr-menu/qr.svg")
def qr_svg(url: str = Query(..., min_length=8, max_length=300), user: dict = Depends(requires("qr_menu"))):
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="The menu address must start with http:// or https://.")
    return Response(qrgen.to_svg(qrgen.encode(url, "Q"), border=4), media_type="image/svg+xml",
                    headers={"Content-Disposition": 'attachment; filename="qr-menu.svg"'})


@router.get("/qr-menu/offline.zip")
def offline_package(user: dict = Depends(requires("qr_menu"))):
    """Everything the onboard laptop needs to serve the menu with no internet."""
    connection = get_connection()
    try:
        data = _public_data(connection)
    finally:
        connection.close()
    used = {i["photo"] for s in data["sections"] for i in s["items"] if i["photo"]}
    used |= {p["image"] for p in data["posters"]}
    for key in ("logo", "banner"):
        if data["settings"].get(key):
            used.add(data["settings"][key])
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("koseli-menu/public/index.html",
                   render_menu(data, "assets/media/", _logo_html(data["settings"], "assets/media/"), "assets/koseli-menu.pdf"))
        from menu_pdf import build_menu_pdf
        z.writestr("koseli-menu/public/assets/koseli-menu.pdf", build_menu_pdf(data, MEDIA_DIR))
        z.writestr("koseli-menu/public/menu.json", json.dumps(data, ensure_ascii=False, indent=1))
        for name in sorted(used):
            path = MEDIA_DIR / name
            if path.is_file():
                z.write(path, f"koseli-menu/public/assets/media/{name}")
        if OFFLINE_DIR.is_dir():
            for path in sorted(OFFLINE_DIR.rglob("*")):
                if path.is_file():
                    z.write(path, f"koseli-menu/{path.relative_to(OFFLINE_DIR).as_posix()}")
        z.write(Path(__file__).resolve().parents[1] / "qrgen.py", "koseli-menu/qrgen.py")
    log_action(user, "QR MENU", "EXPORT_OFFLINE", "Downloaded the offline QR menu package.", None, None)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    return Response(buffer.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="koseli-menu-offline-{stamp}.zip"'})
