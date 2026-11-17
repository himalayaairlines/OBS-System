"""The Koseli onboard menu as printed (sections, items, prices, photos, posters).

Loaded into the QR menu the first time OBS starts with an empty menu, and
again when an administrator chooses "Restore the printed Koseli menu".
Photos are bundled in backend/menu_assets and copied to the media folder.
"""

import shutil
from pathlib import Path

ASSETS = Path(__file__).resolve().parent / "menu_assets"

SETTINGS = {
    "title_en": "Koseli", "title_zh": "Koseli 机上精选",
    "subtitle_en": "Himalaya Airlines onboard menu", "subtitle_zh": "喜马拉雅航空机上菜单",
    "note_en": "Explore the Koseli On board sales menu, pre-purchase your favorites at the sales counter, present your receipt onboard, and enjoy your journey with Himalaya Airlines.",
    "note_zh": "浏览 Koseli 机上销售菜单，在销售柜台预购您喜爱的商品，登机后出示收据，与喜马拉雅航空一起享受旅程。",
    "footer_en": (
        "Passengers can purchase maximum 4 cans of 330ml beer, or 3 cans of 500ml beer, or 2 miniature bottles of whiskey/vodka.\n"
        "यात्रुहरूले ३३० मिलिलिटरको ४ क्यान बियर, वा ५०० मिलिलिटरको ३ क्यान बियर, वा २ मिनिएचर ह्वीस्की वा भोड्काका बोतलहरू मात्र किन्न पाउनु हुने छ\n"
        "H9 may refuse to sale alcoholic beverages at the cabin crew's discretion for passenger safety.\n"
        "H9 ले यात्रुको सुरक्षालाई प्राथमिकता दिँदै मदिरा सेवाको बिक्री अस्वीकृत गर्न सक्दछ\n"
        "For onboard sales we accept NPR, MYR, AED, QAR, USD, SAR, INR, KWD, EUR, GBP, CNY, AUD, JPY & SGD. Please note: Online payments are not available.\n"
        "हामीले अनबोर्ड बिक्रीका लागि NPR, MYR, AED, QAR, USD, SAR, INR, KWD, EUR, GBP, CNY, AUD, JPY / SGD स्वीकार गर्दछौं कृपया ध्यान दिनुहोस् अनलाइन भुक्तानी उपलब्ध छैन\n"
        "All our onboard purchase are non-refundable.\n"
        "खरिद गरिएको सामान फिर्ता हुने छैन"
    ),
    "footer_zh": (
        "每位乘客最多可购买 4 罐 330 毫升啤酒，或 3 罐 500 毫升啤酒，或 2 小瓶威士忌/伏特加。\n"
        "为保障乘客安全，H9 乘务员有权拒绝出售酒精饮品。\n"
        "机上销售接受 NPR、MYR、AED、QAR、USD、SAR、INR、KWD、EUR、GBP、CNY、AUD、JPY 和 SGD。请注意：不支持在线支付。\n"
        "机上购买的商品均不可退换。"
    ),
    "currency": "NPR", "logo": "logo.jpg", "banner": "header.jpg",
}

# (section EN, section ZH, [(name EN, name ZH, price, photo, description EN, description ZH)])
SECTIONS = [
    ("Dashain Dhamaka Offer", "达赛节特惠", [
        ("Playing Cards + Carlsberg Elephant 500ml", "扑克牌 + 嘉士伯大象啤酒 500毫升", 1200, "offer-cards-carlsberg.jpg", "", ""),
        ("Cashew + Carlsberg Elephant 500ml", "腰果 + 嘉士伯大象啤酒 500毫升", 1300, "offer-cashew-carlsberg.jpg", "", ""),
        ("Red Bull + Chips + Playing Cards", "红牛 + 薯片 + 扑克牌", 1400, "offer-redbull-chips-cards.jpg", "", ""),
        ("Carlsberg Elephant 500ml + Chips + Playing Cards", "嘉士伯大象啤酒 500毫升 + 薯片 + 扑克牌", 1600, "offer-carlsberg-chips-cards.jpg", "", ""),
    ]),
    ("Snacks", "小吃", [
        ("Cup Noodles (Wai Wai/Current)", "杯面（Wai Wai / Current）", 200, "cup-noodles.jpg", "Hot water served on board.", "机上提供热水。"),
        ("Buff / Chicken Sitan (50gm)", "水牛肉 / 鸡肉干 Sitan（50克）", 300, "sitan.jpg", "Nepali dried meat snack.", "尼泊尔风味肉干。"),
        ("Fish Sitan (50gm)", "鱼干 Sitan（50克）", 250, "sitan.jpg", "Nepali dried fish snack.", "尼泊尔风味鱼干。"),
        ("Potato Chips (40gm)", "薯片（40克）", 100, "chips.jpg", "", ""),
        ("Party Snacks (40gm)", "派对小食（40克）", 80, "party-snacks.jpg", "", ""),
        ("Salted Cashew Nuts (40gm)", "盐焗腰果（40克）", 550, "cashew.jpg", "", ""),
    ]),
    ("Drinks", "饮品", [
        ("Bottled Drinking Water (500ml)", "瓶装饮用水（500毫升）", 100, "water.jpg", "", ""),
        ("Nescafe", "雀巢咖啡", 150, "nescafe.jpg", "Hot coffee.", "热咖啡。"),
        ("Coke / Sprite (250ml pet bottle)", "可乐 / 雪碧（250毫升）", 150, "coke-sprite.jpg", "", ""),
        ("Redbull (250ml)", "红牛（250毫升）", 300, "redbull.jpg", "", ""),
        ("Canned Fruit Juice", "罐装果汁", 400, "canned-juice.jpg", "OKF pink peach or musk melon.", "OKF 水蜜桃或哈密瓜。"),
        ("Canned Coffee", "罐装咖啡", 400, "canned-coffee.jpg", "OKF caffe latte.", "OKF 拿铁咖啡。"),
        ("Smoothie", "果昔", 500, "smoothie.jpg", "OKF smoothie, assorted flavours.", "OKF 果昔，多种口味。"),
    ]),
    ("Beer & Spirits", "啤酒与烈酒", [
        ("Gorkha Can Beer (500ml)", "廓尔喀啤酒（500毫升）", 900, "gorkha-beer.jpg", "", ""),
        ("Khukuri Rum (90ml)", "库克里朗姆酒（90毫升）", 950, "rum.jpg", "", ""),
        ("Yeti Vodka (90ml)", "雪人伏特加（90毫升）", 800, "vodka.jpg", "", ""),
        ("Gurkhas & Guns Whiskey (90ml)", "Gurkhas & Guns 威士忌（90毫升）", 1000, "whiskey.jpg", "", ""),
    ]),
    ("Souvenirs & Games", "纪念品与游戏", [
        ("Nepali Momo Fridge Magnet", "尼泊尔馍馍冰箱贴", 330, "momo-magnet.jpg", "", ""),
        ("Bhaktapur Durbar Square Embossed Fridge Magnet", "巴克塔普尔杜巴广场浮雕冰箱贴", 270, "bhaktapur-magnet.jpg", "", ""),
        ("Buddha Eyes Himalayan Map of Nepal Fridge Magnet", "佛眼喜马拉雅尼泊尔地图冰箱贴", 270, "map-magnet.jpg", "", ""),
        ("Nepali Flag Pin Badge", "尼泊尔国旗徽章", 270, "flag-pin.jpg", "", ""),
        ("Om Mani Prayer Flag Mini", "迷你六字真言经幡", 130, "om-flag.jpg", "", ""),
        ("Rudraksha / Buddhachitta Bracelet", "金刚菩提 / 菩提子手串", 450, "bracelet.jpg", "", ""),
        ("Playing Cards", "扑克牌", 900, "cards.jpg", "", ""),
    ]),
    ("Combo Offers", "套餐优惠", [
        ("Nepali Momo Magnet + Bhaktapur Durbar Square Magnet", "馍馍冰箱贴 + 杜巴广场冰箱贴", 500, "momo-bhaktapur.jpg", "", ""),
        ("Buddha Eyes Himalayan Map Magnet + Nepali Flag Pin Badge", "佛眼地图冰箱贴 + 尼泊尔国旗徽章", 450, "map-flagpin.jpg", "", ""),
        ("Nepali Momo Magnet + Buddha Eyes Himalayan Map Magnet + Nepali Flag Pin Badge", "馍馍冰箱贴 + 佛眼地图冰箱贴 + 国旗徽章", 750, "momo-map-pin.jpg", "", ""),
        ("Rudraksha Bracelet + Om Mani Prayer Flag Mini", "金刚菩提手串 + 迷你经幡", 450, "rudraksha-flag.jpg", "", ""),
        ("Buddhachitta Bracelet + Om Mani Prayer Flag Mini", "菩提子手串 + 迷你经幡", 450, "buddhachitta-flag.jpg", "", ""),
        ("Salted Cashew + Canned Juice", "盐焗腰果 + 罐装果汁", 850, "combo-cashew-juice.jpg", "", ""),
        ("Salted Cashew + Canned Coffee", "盐焗腰果 + 罐装咖啡", 850, "combo-cashew-coffee.jpg", "", ""),
        ("Salted Cashew + Smoothie", "盐焗腰果 + 果昔", 900, "combo-cashew-smoothie.jpg", "", ""),
        ("Playing Cards + Canned Coffee", "扑克牌 + 罐装咖啡", 1100, "combo-cards-coffee.jpg", "", ""),
        ("Playing Cards + Smoothie", "扑克牌 + 果昔", 1150, "combo-cards-smoothie.jpg", "", ""),
        ("Playing Cards + Canned Juice", "扑克牌 + 罐装果汁", 1100, "combo-cards-juice.jpg", "", ""),
    ]),
]

POSTERS = [
    ("Souvenirs", "poster-souvenirs.jpg"),
    ("Snacks & drinks", "poster-snacks-drinks.jpg"),
    ("Dashain Dhamaka offer", "poster-dashain-offer.jpg"),
    ("Combo offers", "poster-combos.jpg"),
]


def menu_is_empty(connection) -> bool:
    for table in ("qr_menu_sections", "qr_menu_items", "qr_menu_posters"):
        if connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
            return False
    return True


def load_koseli_menu(connection, media_dir: Path, by: str = "system") -> dict:
    """Replace the whole menu with the printed Koseli menu. Commits."""
    from datetime import datetime
    media_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    if ASSETS.is_dir():
        for src in ASSETS.glob("*.jpg"):
            shutil.copyfile(src, media_dir / src.name)
            copied += 1
    now = datetime.now().replace(microsecond=0).isoformat()
    connection.execute("DELETE FROM qr_menu_items")
    connection.execute("DELETE FROM qr_menu_sections")
    connection.execute("DELETE FROM qr_menu_posters")
    for key, value in SETTINGS.items():
        connection.execute("INSERT INTO qr_menu_settings(key,value) VALUES (?,?) "
                           "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    items = 0
    for order, (name_en, name_zh, entries) in enumerate(SECTIONS, start=1):
        section_id = connection.execute(
            "INSERT INTO qr_menu_sections(name_en,name_zh,sort_order,active,updated_by,updated_at) VALUES (?,?,?,1,?,?)",
            (name_en, name_zh, order, by, now)).lastrowid
        for i, (en, zh, price, photo, desc_en, desc_zh) in enumerate(entries, start=1):
            connection.execute(
                "INSERT INTO qr_menu_items(section_id,name_en,name_zh,desc_en,desc_zh,price,photo,sort_order,active,updated_by,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,1,?,?)",
                (section_id, en, zh, desc_en or None, desc_zh or None, price, photo, i, by, now))
            items += 1
    for i, (title, image) in enumerate(POSTERS, start=1):
        connection.execute(
            "INSERT INTO qr_menu_posters(title,image,sort_order,active,updated_by,updated_at) VALUES (?,?,?,1,?,?)",
            (title, image, i, by, now))
    connection.commit()
    return {"sections": len(SECTIONS), "items": items, "posters": len(POSTERS), "photos": copied}
