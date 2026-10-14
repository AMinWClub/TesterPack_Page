"""Display-only Persian mappings. Unknown specialist names keep their original text."""

import ipaddress
import math
import re
from urllib.parse import urlsplit


ACCORDS = {
    "warm spicy": "ادویه‌ای گرم", "fresh spicy": "ادویه‌ای تازه", "soft spicy": "ادویه‌ای ملایم",
    "woody": "چوبی", "sweet": "شیرین", "vanilla": "وانیلی", "amber": "کهربایی",
    "aromatic": "آروماتیک", "powdery": "پودری", "musky": "مشکی", "floral": "گلی",
    "white floral": "گل‌های سفید", "yellow floral": "گل‌های زرد", "citrus": "مرکباتی",
    "fruity": "میوه‌ای", "leather": "چرمی", "green": "سبز", "aquatic": "آبزی",
    "alcohol": "الکلی", "aldehydic": "آلدهیدی", "almond": "بادامی", "animalic": "حیوانی",
    "anis": "انیسونی", "balsamic": "بالزامیک", "beeswax": "موم زنبور", "bitter": "تلخ",
    "cacao": "کاکائویی", "camphor": "کافوری", "cannabis": "شاهدانه", "caramel": "کاراملی",
    "cherry": "گیلاسی", "chocolate": "شکلاتی", "cinnamon": "دارچینی", "coconut": "نارگیلی",
    "coffee": "قهوه‌ای", "conifer": "درختان سوزنی‌برگ", "earthy": "خاکی", "fresh": "تازه",
    "herbal": "گیاهی", "honey": "عسلی", "iris": "زنبقی", "lactonic": "شیری",
    "lavender": "اسطوخودوسی", "marine": "دریایی", "metallic": "فلزی", "mineral": "معدنی",
    "mossy": "خزه‌ای", "nutty": "آجیلی", "oud": "عودی", "ozonic": "اوزونی",
    "patchouli": "نعناع هندی", "plastic": "پلاستیکی", "rose": "رز", "rubber": "لاستیکی",
    "rum": "رام", "salty": "شور", "savory": "خوراکی و غیرشیرین", "smoky": "دودی",
    "soapy": "صابونی", "sour": "ترش", "terpenic": "ترپنی", "tobacco": "تنباکویی",
    "tropical": "گرمسیری", "tuberose": "گل مریم", "vinyl": "وینیلی", "violet": "بنفشه‌ای",
    "vodka": "ودکا", "whiskey": "ویسکی",
}

NOTES = {
    "patchouli": "نعناع هندی", "musk": "مشک", "bergamot": "ترنج", "sandalwood": "چوب صندل",
    "vanilla": "وانیل", "jasmine": "یاس", "amber": "کهربا", "cedar": "چوب سدر", "rose": "رز",
    "vetiver": "وتیور", "tonka bean": "دانه تونکا", "leather": "چرم", "pink pepper": "فلفل صورتی",
    "lavender": "اسطوخودوس", "orange blossom": "شکوفه پرتقال", "mandarin orange": "نارنگی",
    "lemon": "لیمو", "agarwood (oud)": "عود", "oud": "عود", "cardamom": "هل",
    "cinnamon": "دارچین", "benzoin": "بنزوئین", "saffron": "زعفران", "grapefruit": "گریپ‌فروت",
    "geranium": "شمعدانی", "labdanum": "لابدانوم", "iris": "زنبق", "incense": "بخور",
    "ginger": "زنجبیل", "violet": "بنفشه", "oakmoss": "خزه بلوط", "white musk": "مشک سفید",
    "woody notes": "نت‌های چوبی", "woodsy notes": "نت‌های چوبی", "ambergris": "عنبر سائل",
    "lily-of-the-valley": "موگه", "lily of the valley": "موگه", "tuberose": "گل مریم",
    "guaiac wood": "چوب گایاک", "nutmeg": "جوز هندی", "pear": "گلابی", "peach": "هلو",
    "black currant": "انگور فرنگی سیاه", "olibanum": "کندر", "frankincense": "کندر",
    "neroli": "نرولی", "tobacco": "تنباکو", "ylang-ylang": "یلانگ یلانگ", "ylang ylang": "یلانگ یلانگ",
    "raspberry": "تمشک", "orange": "پرتقال", "pepper": "فلفل", "ambroxan": "آمبروکسان",
    "coriander": "گشنیز", "jasmine sambac": "یاس سامباک", "virginia cedar": "سدر ویرجینیا",
    "virginian cedar": "سدر ویرجینیا", "peony": "گل صدتومانی", "heliotrope": "گل آفتاب‌پرست",
    "apple": "سیب", "violet leaf": "برگ بنفشه", "bulgarian rose": "رز بلغاری", "lime": "لیموترش سبز",
    "clary sage": "مریم‌گلی کلاری", "sage": "مریم‌گلی", "black pepper": "فلفل سیاه",
    "magnolia": "مگنولیا", "gardenia": "گاردنیا", "rosemary": "رزماری", "freesia": "فریزیا",
    "cypress": "سرو", "madagascar vanilla": "وانیل ماداگاسکار", "mint": "نعناع", "orris": "زنبق",
    "bitter orange": "نارنج", "amberwood": "چوب عنبری", "green notes": "نت‌های سبز", "rum": "رام",
    "caramel": "کارامل", "osmanthus": "اسمانتوس", "ambrette (musk mallow)": "آمبرت (ختمی مشک)",
    "ambrette": "آمبرت", "coffee": "قهوه", "orris root": "ریشه زنبق", "honey": "عسل",
    "cashmeran": "کشمران", "basil": "ریحان", "myrrh": "مر مکی", "myrhh": "مر مکی",
    "spices": "ادویه‌ها", "bourbon vanilla": "وانیل بوربون", "cashmere wood": "چوب کشمیر",
    "pineapple": "آناناس", "calabrian bergamot": "ترنج کالابریا", "cacao": "کاکائو", "cocoa": "کاکائو",
    "plum": "آلو", "citruses": "مرکبات", "turkish rose": "رز ترکی", "birch": "توس",
    "vanille": "وانیل", "vanila": "وانیل", "almond": "بادام", "orchid": "ارکیده",
    "juniper berries": "میوه ارس", "cedarwood": "چوب سدر", "coconut": "نارگیل",
    "cypriol oil or nagarmotha": "روغن سیپریول (ناگارموتا)", "cypriol": "سیپریول",
    "galbanum": "گالبانوم", "moss": "خزه", "tangerine": "نارنگی", "praline": "پرالین",
    "petitgrain": "پتی‌گرن", "styrax": "استیراکس", "amalfi lemon": "لیمو آمالفی", "suede": "جیر",
    "artemisia": "درمنه", "papyrus": "پاپیروس", "haitian vetiver": "وتیور هائیتی", "elemi": "المی",
    "lily": "سوسن", "sea notes": "نت‌های دریایی", "litchi": "لیچی", "lychee": "لیچی",
    "cloves": "میخک", "clove": "میخک", "aldehydes": "آلدهیدها", "may rose": "رز مه",
    "apricot": "زردآلو", "lotus": "نیلوفر آبی", "rose water": "گلاب", "bitter almond": "بادام تلخ",
    "hazelnut": "فندق", "iris pallida": "زنبق پالیدا", "indian tuberose": "گل مریم هندی",
    "green mandarin": "نارنگی سبز", "black tea": "چای سیاه", "indonesian patchouli leaf": "برگ نعناع هندی اندونزی",
    "vanilla absolute": "ابسلوت وانیل", "tonka": "تونکا", "sugar": "شکر", "cherry": "گیلاس",
    "sour cherry": "آلبالو", "chocolate": "شکلات", "dark chocolate": "شکلات تلخ", "milk": "شیر",
    "green apple": "سیب سبز", "red apple": "سیب قرمز", "blackberry": "شاه‌توت", "strawberry": "توت‌فرنگی",
    "fig": "انجیر", "fig leaf": "برگ انجیر", "pomegranate": "انار", "melon": "خربزه",
    "watermelon": "هندوانه", "passionfruit": "پشن‌فروت", "mango": "انبه", "yuzu": "یوزو",
    "cumin": "زیره", "anise": "انیسون", "star anise": "بادیان ستاره‌ای", "sichuan pepper": "فلفل سیچوان",
    "eucalyptus": "اکالیپتوس", "juniper": "ارس", "thyme": "آویشن", "tarragon": "ترخون",
    "tea": "چای", "green tea": "چای سبز", "white tea": "چای سفید", "salt": "نمک", "sea salt": "نمک دریا",
    "sea water": "آب دریا", "water notes": "نت‌های آبی", "watery notes": "نت‌های آبی",
    "french labdanum": "لابدانوم فرانسوی", "spanish labdanum": "لابدانوم اسپانیایی",
    "rose oil": "روغن رز", "rose de mai": "رز مه", "damask rose": "رز دمشقی", "taif rose": "رز طائف",
    "sandalowood": "چوب صندل", "australian sandalwood": "چوب صندل استرالیایی",
    "mysore sandalwood": "چوب صندل میسور", "white sandalwood": "چوب صندل سفید",
    "atlas cedar": "سدر اطلس", "texas cedar": "سدر تگزاس", "cedar essence": "اسانس سدر",
    "sicilian bergamot": "ترنج سیسیلی", "sicilian lemon": "لیمو سیسیلی", "italian lemon": "لیمو ایتالیایی",
    "egyptian jasmine": "یاس مصری", "indian jasmine": "یاس هندی", "moroccan jasmine": "یاس مراکشی",
    "natural vanilla": "وانیل طبیعی", "vanilla bean": "غلاف وانیل", "vanilla orchid": "ارکیده وانیل",
    "tahitian vanilla": "وانیل تاهیتی", "indian vanilla": "وانیل هندی", "vanillin": "وانیلین",
    "tobacco leaf": "برگ تنباکو", "white tobacco": "تنباکوی سفید", "black musk": "مشک سیاه",
    "white flowers": "گل‌های سفید", "floral notes": "نت‌های گلی", "fruity notes": "نت‌های میوه‌ای",
    "powdery notes": "نت‌های پودری", "sweet notes": "نت‌های شیرین", "herbal notes": "نت‌های گیاهی",
    "spicy notes": "نت‌های ادویه‌ای", "smoke": "دود", "resins": "رزین‌ها", "beeswax": "موم زنبور",
    "pine": "کاج", "pine tree": "درخت کاج", "red berries": "میوه‌های قرمز", "dried fruits": "میوه‌های خشک",
    "coconut milk": "شیر نارگیل", "coconut water": "آب نارگیل", "ginger flower": "گل زنجبیل",
    "tunisian orange blossom": "شکوفه پرتقال تونسی", "tunisian neroli": "نرولی تونسی",
    "african orange flower": "شکوفه پرتقال آفریقایی", "chinese orange": "پرتقال چینی",
    "sicilian orange": "پرتقال سیسیلی", "italian orange": "پرتقال ایتالیایی",
    "italian mandarin": "نارنگی ایتالیایی", "sicilian mandarin": "نارنگی سیسیلی",
    "indian mandarin": "نارنگی هندی", "blood mandarin": "نارنگی خونی", "blood orange": "پرتقال خونی",
    "black cherry": "گیلاس سیاه", "green pepper": "فلفل سبز", "white pepper": "فلفل سفید",
    "black cardamom": "هل سیاه", "guatemalan cardamom": "هل گواتمالا", "cardamon": "هل",
    "bourbon pepper": "فلفل بوربون", "bourbon vetiver": "وتیور بوربون", "madagascar vetiver": "وتیور ماداگاسکار",
    "tahitian vetiver": "وتیور تاهیتی", "java vetiver oil": "روغن وتیور جاوه",
    "italian jasmine": "یاس ایتالیایی", "chinese jasmine": "یاس چینی", "italian iris": "زنبق ایتالیایی",
    "tuscan iris": "زنبق توسکانی", "grasse rose": "رز گراس", "white rose": "رز سفید", "black rose": "رز سیاه",
    "rose hip": "میوه نسترن", "tincture of rose": "تنتور رز", "cinnamon leaf": "برگ دارچین",
    "ceylon cinnamon": "دارچین سیلان", "lemon leaf": "برگ لیمو", "lemon blossom": "شکوفه لیمو",
    "mandarin leaf": "برگ نارنگی", "bergamot leaf": "برگ ترنج", "patchouli leaf": "برگ نعناع هندی",
    "indian patchouli": "پچولی هند", "nigerian ginger": "زنجبیل نیجریه‌ای",
    "chinese black tea": "چای سیاه چینی", "tea leaf": "برگ چای",
    "almond milk": "شیر بادام", "almond blossom": "شکوفه بادام", "cacao pod": "غلاف کاکائو",
    "cacao butter": "کره کاکائو", "brown sugar": "شکر قهوه‌ای", "sugar cane": "نیشکر",
    "red fruits": "میوه‌های قرمز", "wild berries": "میوه‌های وحشی", "wild strawberry": "توت‌فرنگی وحشی",
    "red currant": "انگور فرنگی قرمز", "green leaves": "برگ‌های سبز",
    "grass": "علف", "seaweed": "جلبک دریایی", "violet root": "ریشه بنفشه", "oak moss": "خزه بلوط",
    "flower petals": "گلبرگ‌ها", "flowers": "گل‌ها", "peach blossom": "شکوفه هلو", "cherry blossom": "شکوفه گیلاس",
    "pear blossom": "شکوفه گلابی", "apple blossom": "شکوفه سیب", "white peach": "هلوی سفید",
    "roasted coffee beans": "دانه‌های قهوه برشته", "chamomile": "بابونه", "marshmallow": "مارشمالو",
    "cucumber": "خیار", "carnation": "میخک صدپر", "mimosa": "میموزا", "lilac": "یاس بنفش",
    "hyacinth": "سنبل", "hibiscus": "ختمی", "pistachio": "پسته", "chestnut": "شاه‌بلوط",
    "rhubarb": "ریواس", "toffee": "تافی", "whipped cream": "خامه زده‌شده", "Animal notes": "نت‌های حیوانی", "Quince": "به"
}

GENDERS = {
    "female": ("زنانه", "زنانه"), "moreFemale": ("متمایل به زنانه", "زنانه"),
    "unisex": ("یونیسکس", "یونیسکس"), "moreMale": ("متمایل به مردانه", "مردانه"), "male": ("مردانه", "مردانه"),
}
SEASONS = {"spring": "بهار", "summer": "تابستان", "autumn": "پاییز", "winter": "زمستان"}
LONGEVITY = {"veryWeak": "خیلی ضعیف", "weak": "ضعیف", "moderate": "متوسط", "longLasting": "ماندگار", "eternal": "بسیار ماندگار"}
SILLAGE = {"intimate": "ملایم", "moderate": "متوسط", "strong": "قوی", "enormous": "بسیار قوی"}


def text(value):
    return value.strip() if isinstance(value, str) else ""


def positive_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def dominant(votes, categories):
    if not isinstance(votes, dict):
        return None
    valid = {key: votes[key] for key in categories if positive_number(votes.get(key))}
    if not valid:
        return None
    winners = [key for key, value in valid.items() if value == max(valid.values())]
    return winners[0] if len(winners) == 1 else None


def gender_display(data):
    key = dominant(data.get("votes") if isinstance(data, dict) else None, GENDERS)
    return GENDERS[key] if key else (None, None)


def season_display(data):
    # Keep seasons with at least 65% of the highest seasonal vote; ignore day/night.
    if not isinstance(data, dict):
        return None
    votes = data.get("votes", data)
    if not isinstance(votes, dict):
        return None
    valid = {key: votes[key] for key in SEASONS if positive_number(votes.get(key))}
    if not valid:
        return None
    selected = [SEASONS[key] for key in SEASONS if key in valid and valid[key] >= max(valid.values()) * .65]
    return "چهار فصل" if len(selected) == 4 else " و ".join(selected)


def translated(name, mapping):
    name = text(name)
    return mapping.get(name.casefold(), name)


def scent_display(preferences, accords):
    preferences = preferences if isinstance(preferences, dict) else {}
    ranked = sorted((a for a in accords if isinstance(a, dict) and positive_number(a.get("strength"))), key=lambda a: a["strength"], reverse=True)
    names = [text(a.get("name")).lower() for a in ranked]
    parts = []
    temperature = {"warm": "گرم", "cool": "خنک"}.get(preferences.get("temperature"))
    if temperature:
        parts.append(temperature)
    if preferences.get("sweetness") == "high":
        parts.append("شیرین")
    elif preferences.get("freshness") == "high":
        parts.append("تازه")
    # Low sweetness alone is never evidence of bitterness.
    for name in names:
        label = ACCORDS.get(name)
        if label and label not in parts and not (temperature == "گرم" and name == "warm spicy"):
            parts.append(label)
        elif temperature == "گرم" and name == "warm spicy":
            parts.append("ادویه‌ای")
        if len(parts) >= 2:
            break
    return " و ".join(parts[:2]) or None


def meter_display(data, categories):
    key = dominant(data.get("votes") if isinstance(data, dict) else None, categories)
    scale = list(categories.values())
    return {"category": key, "label": categories[key] if key else "نامشخص", "level": list(categories).index(key) + 1 if key else None, "scale": scale}


def safe_image_url(value):
    value = text(value)
    if not value or any(char.isspace() or ord(char) < 32 for char in value) or "\\" in value:
        return None
    try:
        url = urlsplit(value)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.port not in (None, 443):
            return None
        host = url.hostname.lower().rstrip(".")
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")) or "." not in host or host.rsplit(".", 1)[-1].isdigit():
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            if not re.fullmatch(r"[a-z0-9.-]+", host):
                return None
        return value
    except ValueError:
        return None


def accord_display(accords):
    result = []
    for accord in accords if isinstance(accords, list) else []:
        if not isinstance(accord, dict) or not text(accord.get("name")) or not positive_number(accord.get("strength")):
            continue
        color = text(accord.get("color"))
        result.append({"name": translated(accord["name"], ACCORDS), "originalName": accord["name"], "strength": accord["strength"], "color": color if re.fullmatch(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})", color) else None})
    return sorted(result, key=lambda a: a["strength"], reverse=True)


def notes_display(notes):
    result = []
    for note in notes if isinstance(notes, list) else []:
        if not isinstance(note, dict) or not text(note.get("name")):
            continue
        note_id = note.get("noteId")
        result.append({"name": translated(note["name"], NOTES), "originalName": note["name"], "noteId": str(note_id) if isinstance(note_id, (str, int)) and not isinstance(note_id, bool) else None, "image": safe_image_url(note.get("imageUrl"))})
    return result
