"""Normalize raw receipt item text into clean, matchable words.

Grocery receipts compress item names aggressively ("BNLS SKLS CHKN BRST",
"GV WHP CRM", "ORG BBY SPIN"). This module expands the common
abbreviations used by major US chains (Walmart, Kroger, Safeway, Costco,
Target, Aldi, Publix) and strips store-brand prefixes, sizes, and noise.
"""

from __future__ import annotations

import re

# Store-brand / private-label prefixes that carry no product information.
STORE_BRAND_PREFIXES: frozenset[str] = frozenset({
    "gv",       # Great Value (Walmart)
    "mm",       # Member's Mark (Sam's Club)
    "ks",       # Kirkland Signature (Costco)
    "kirkland",
    "hcf",      # Hill Country Fare (HEB)
    "heb",
    "kro",      # Kroger
    "kroger",
    "sig",      # Signature Select (Safeway)
    "st",       # Simple Truth (Kroger)
    "pub",      # Publix
    "publix",
    "mp",       # Market Pantry (Target)
    "gg",       # Good & Gather (Target)
    "wf",       # Whole Foods
    "365",      # 365 by Whole Foods
    "tj",       # Trader Joe's
    "tjs",
    "pvt",      # private label
    "wm",       # Walmart
    "eq",       # Equate (Walmart)
    "os",       # Our Selection
})

# Words that describe packaging/size/marketing, not the product itself.
NOISE_WORDS: frozenset[str] = frozenset({
    "ea", "each", "lb", "lbs", "oz", "floz", "fl", "ct", "count", "pk", "pack",
    "pkg", "gal", "gallon", "qt", "quart", "pt", "pint", "ltr", "liter", "ml",
    "gram", "grams", "kg", "g", "sz", "size", "asst", "assorted", "variety",
    "value", "family", "party", "club", "bonus", "mega", "jumbo", "giant",
    "select", "premium", "classic", "original", "traditional", "regular",
    "reg", "brand", "new", "improved", "sale", "special", "deal",
    "the", "a", "an", "of", "with", "w", "and", "in", "no", "non", "style",
})

# Abbreviation -> expansion, applied token-by-token after cleanup.
# Multi-word expansions are allowed ("pb" -> "peanut butter").
ABBREVIATIONS: dict[str, str] = {
    # Meat & poultry
    "chkn": "chicken", "chckn": "chicken", "chk": "chicken", "chick": "chicken",
    "ckn": "chicken", "chix": "chicken",
    "brst": "breast", "brsts": "breasts", "bnls": "boneless", "bnlss": "boneless",
    "sklss": "skinless", "skls": "skinless",
    "thgh": "thighs", "thghs": "thighs", "drmstk": "drumsticks",
    "wngs": "wings", "wng": "wings",
    "grnd": "ground", "grd": "ground", "gb": "ground beef",
    "bf": "beef",
    "prk": "pork", "chp": "chops", "chps": "chops",
    "tndrloin": "tenderloin", "tndr": "tenderloin", "ln": "loin",
    "rst": "roast", "chck": "chuck", "rbye": "ribeye", "srloin": "sirloin",
    "stk": "steak", "stks": "steaks",
    "trky": "turkey", "trk": "turkey", "tky": "turkey",
    "sausg": "sausage", "ssg": "sausage", "saus": "sausage",
    "bcn": "bacon", "hm": "ham", "rtssr": "rotisserie", "rotis": "rotisserie",
    "mt": "meat", "pepprni": "pepperoni",
    # Seafood
    "slmn": "salmon", "shrmp": "shrimp", "flt": "fillet", "flts": "fillets",
    "fil": "fillet", "tlpa": "tilapia", "sfd": "seafood",
    # Dairy & eggs
    "mlk": "milk", "whl": "whole", "rdcd": "reduced", "ff": "fat free",
    "lf": "low fat", "skm": "skim", "vit": "vitamin",
    "chz": "cheese", "chs": "cheese", "cheez": "cheese", "chse": "cheese",
    "chdr": "cheddar", "chd": "cheddar", "shrp": "sharp", "mld": "mild",
    "mozz": "mozzarella", "mozzarela": "mozzarella", "parm": "parmesan",
    "shrd": "shredded", "shred": "shredded", "slcd": "sliced", "slc": "sliced",
    "crm": "cream", "whp": "whipping", "whpd": "whipped", "hvy": "heavy",
    "hlf": "half", "crmr": "creamer",
    "yog": "yogurt", "ygrt": "yogurt", "ygt": "yogurt", "grk": "greek",
    "bttr": "butter", "btr": "butter", "unsltd": "unsalted", "sltd": "salted",
    "mrgrn": "margarine",
    "lg": "large", "xl": "extra large", "dz": "dozen", "doz": "dozen",
    "gra": "grade a", "ctg": "cottage", "sr": "sour",
    # Produce
    "org": "organic", "orgnc": "organic",
    "ban": "bananas", "bnna": "bananas", "bnans": "bananas", "bnn": "bananas",
    "appl": "apples", "apls": "apples",
    "orng": "oranges", "orngs": "oranges", "navl": "navel",
    "strw": "strawberries", "strwb": "strawberries", "strwbry": "strawberries",
    "strwbrry": "strawberries", "strawb": "strawberries",
    "blubry": "blueberries", "blbry": "blueberries",
    "rasp": "raspberries", "rspbry": "raspberries",
    "blkbry": "blackberries",
    "grps": "grapes", "grp": "grapes", "sdlss": "seedless",
    "wtrmln": "watermelon", "cntlp": "cantaloupe", "pnappl": "pineapple",
    "avcd": "avocado", "avo": "avocado", "avoc": "avocado",
    "lmn": "lemons", "lmns": "lemons", "lms": "limes",
    "grpfrt": "grapefruit", "mndrn": "mandarins", "clemen": "clementines",
    "pch": "peaches", "pchs": "peaches", "nctrn": "nectarines",
    "chrry": "cherries",
    "tom": "tomatoes", "toms": "tomatoes", "tmto": "tomatoes", "tmt": "tomatoes",
    "rma": "roma", "chry": "cherry",
    "pot": "potatoes", "pots": "potatoes", "potat": "potatoes", "ptto": "potatoes",
    "russt": "russet", "rsst": "russet", "swt": "sweet", "ykn": "yukon",
    "onin": "onions", "onn": "onions", "onns": "onions",
    "yel": "yellow", "ylw": "yellow", "wht": "white", "rd": "red", "grn": "green",
    "grlc": "garlic", "grl": "garlic",
    "crrt": "carrots", "crrts": "carrots", "crt": "carrots", "bby": "baby",
    "clry": "celery", "brcli": "broccoli", "brcc": "broccoli", "brc": "broccoli",
    "clflwr": "cauliflower", "cauli": "cauliflower",
    "pppr": "peppers", "pepp": "peppers",
    "jlpno": "jalapenos", "jal": "jalapenos",
    "ccmbr": "cucumbers", "cuc": "cucumbers", "cucs": "cucumbers",
    "zcchn": "zucchini", "zucc": "zucchini", "sqsh": "squash", "bttrnt": "butternut",
    "mshrm": "mushrooms", "mush": "mushrooms", "mshrms": "mushrooms",
    "asprgs": "asparagus", "asp": "asparagus",
    "bns": "beans",
    "brssl": "brussels", "sprts": "sprouts",
    "lttc": "lettuce", "ltc": "lettuce", "lett": "lettuce", "icbrg": "iceberg",
    "rmn": "romaine", "rom": "romaine", "hrts": "hearts",
    "spnch": "spinach", "spin": "spinach", "spn": "spinach",
    "sld": "salad", "spr": "spring", "mx": "mix", "mxd": "mixed",
    "cbbg": "cabbage", "cab": "cabbage",
    "cilntr": "cilantro", "cil": "cilantro", "prsly": "parsley",
    "grns": "greens", "vggs": "vegetables", "veg": "vegetables",
    "vegs": "vegetables", "vegtbl": "vegetables", "vgtbl": "vegetables",
    "frt": "fruit", "frts": "fruits",
    "mngo": "mango", "kwi": "kiwi", "prs": "pears",
    "plm": "plums", "plms": "plums", "pom": "pomegranate",
    "rdsh": "radishes", "bts": "beets", "gngr": "ginger", "egplnt": "eggplant",
    # Bakery
    "brd": "bread", "sndwch": "sandwich", "sndw": "sandwich",
    "bgl": "bagels", "bgls": "bagels",
    "tort": "tortillas", "trtla": "tortillas", "trtlla": "tortillas",
    "flr": "flour", "crn": "corn",
    "hmbrgr": "hamburger", "hmb": "hamburger", "hdg": "hot dog",
    "mffn": "muffins", "mffns": "muffins", "engl": "english",
    "crssnt": "croissants", "dnt": "donuts", "dnts": "donuts",
    "ckies": "cookies", "ckys": "cookies", "cke": "cake",
    "srdgh": "sourdough", "bagt": "baguette", "frnch": "french",
    "rlls": "rolls", "dnnr": "dinner",
    "whlwht": "whole wheat", "mltgrn": "multigrain",
    # Frozen
    "frzn": "frozen", "frz": "frozen", "fz": "frozen",
    "icecrm": "ice cream", "icrm": "ice cream", "van": "vanilla",
    "choc": "chocolate", "chc": "chocolate",
    "pzza": "pizza", "pza": "pizza",
    "nggts": "nuggets", "nugg": "nuggets",
    "wffls": "waffles", "wffl": "waffles",
    "frys": "fries",
    "brrto": "burritos", "dmplng": "dumplings",
    # Pantry
    "spghtti": "spaghetti", "spag": "spaghetti", "pnne": "penne", "mac": "macaroni",
    "ndl": "noodles", "ndls": "noodles",
    "rce": "rice", "jsmn": "jasmine", "bsmti": "basmati", "brwn": "brown",
    "sgr": "sugar", "grnltd": "granulated", "pwdrd": "powdered",
    "slt": "salt", "blk": "black",
    "evoo": "olive oil", "olv": "olive", "vgtble": "vegetable", "cnla": "canola",
    "crl": "cereal", "otml": "oatmeal",
    "pnt": "peanut", "pb": "peanut butter", "jly": "jelly", "jlly": "jelly",
    "hny": "honey", "syrp": "syrup", "mpl": "maple",
    "pncke": "pancake", "pnck": "pancake",
    "coff": "coffee", "cffee": "coffee", "cff": "coffee",
    "almnds": "almonds", "pnts": "peanuts", "cshws": "cashews",
    "rsns": "raisins", "crnbrry": "cranberry", "crnbrries": "cranberries",
    "qnoa": "quinoa", "lentl": "lentils",
    "bkng": "baking", "sda": "soda", "pwdr": "powder", "vnlla": "vanilla",
    "extrct": "extract",
    # Canned
    "cnd": "canned", "dcd": "diced", "crshd": "crushed",
    "pst": "paste", "sce": "sauce",
    "tna": "tuna", "chnk": "chunk", "lt": "light",
    "ndle": "noodle", "brth": "broth", "stck": "stock",
    "kdny": "kidney", "grbnzo": "garbanzo", "chkpeas": "chickpeas",
    "rfrd": "refried", "bkd": "baked",
    "ccnut": "coconut",
    # Beverages
    "jce": "juice", "juic": "juice", "oj": "orange juice", "aj": "apple juice",
    "drnk": "drink", "drnks": "drinks", "bev": "beverage",
    "wtr": "water", "sprklng": "sparkling", "sltzr": "seltzer", "sprng": "spring",
    "btld": "bottled", "btl": "bottle",
    "lmnade": "lemonade", "lmnd": "lemonade",
    "engy": "energy", "kmbcha": "kombucha",
    # Snacks
    "chip": "chips", "ptato": "potato",
    "prtzl": "pretzels", "prtzls": "pretzels", "ppcrn": "popcorn",
    "crckrs": "crackers", "crkr": "crackers", "grhm": "graham",
    "grnla": "granola", "brs": "bars", "prtn": "protein",
    "cndy": "candy", "gmmy": "gummy", "jrky": "jerky",
    "applsce": "applesauce", "pddng": "pudding", "trlmx": "trail mix",
    # Condiments
    "ktchp": "ketchup", "ktch": "ketchup", "catsup": "ketchup",
    "mstrd": "mustard", "mst": "mustard", "djn": "dijon",
    "myo": "mayonnaise", "mayo": "mayonnaise", "mynse": "mayonnaise",
    "rnch": "ranch", "drssng": "dressing", "drsng": "dressing", "dress": "dressing",
    "csr": "caesar", "itln": "italian", "itl": "italian",
    "brbq": "barbecue",
    "srrcha": "sriracha", "ht": "hot",
    "sy": "soy", "tryki": "teriyaki", "wrcstr": "worcestershire",
    "slsa": "salsa", "mrnra": "marinara", "alfrdo": "alfredo",
    "pckls": "pickles", "pckl": "pickles", "dll": "dill",
    "vngr": "vinegar", "cdr": "cider", "blsmc": "balsamic",
    "olvs": "olives", "rlsh": "relish",
    # Household / misc
    "twls": "towels", "twl": "towels",
    "tp": "toilet paper", "tlt": "toilet", "tissu": "tissue", "bth": "bath",
    "dtrgnt": "detergent", "lndry": "laundry", "dsh": "dish",
    "trsh": "trash", "grbg": "garbage", "bgs": "bags",
    "alum": "aluminum", "plstc": "plastic", "wrp": "wrap",
    "znoc": "ziploc",
    "npkns": "napkins", "shmpoo": "shampoo", "tthpst": "toothpaste",
    "dodrnt": "deodorant", "battry": "batteries", "btts": "batteries",
    "blb": "bulbs", "bulb": "bulbs", "clnr": "cleaner", "wps": "wipes",
    "dsnfctng": "disinfecting", "blch": "bleach", "spngs": "sponges",
    "dg": "dog", "fd": "food",
    "frml": "formula", "infnt": "infant", "vitmns": "vitamins",
    "mltvtmn": "multivitamin",
}

_PUNCT_RE = re.compile(r"[^a-z0-9\s]")
_MULTI_SPACE_RE = re.compile(r"\s+")
_DIGIT_TOKEN_RE = re.compile(
    r"^\d+(\.\d+)?%?$"
    r"|^\d+(\.\d+)?(pk|ct|oz|floz|lb|lbs|g|kg|ml|l|z|gal|qt|pt|ltr|liter|pc|pcs|rolls?|dz)$"
)


def tokenize(text: str) -> list[str]:
    """Lowercase, strip punctuation, and split into tokens."""
    text = text.lower()
    text = _PUNCT_RE.sub(" ", text)
    text = _MULTI_SPACE_RE.sub(" ", text).strip()
    return text.split()


def expand_token(token: str) -> str:
    """Expand a single receipt abbreviation, if known."""
    return ABBREVIATIONS.get(token, token)


def normalize(text: str) -> str:
    """Turn raw receipt item text into clean, expanded, lowercase words.

    >>> normalize("GV WHP CRM 16OZ")
    'whipping cream'
    >>> normalize("BNLS SKLS CHKN BRST")
    'boneless skinless chicken breast'
    """
    tokens = tokenize(text)
    out: list[str] = []
    for i, tok in enumerate(tokens):
        # Drop store-brand prefixes only at the start of the name.
        if i == 0 and tok in STORE_BRAND_PREFIXES:
            continue
        # Drop pure numbers and size tokens like "16oz", "2pk", "12ct".
        if _DIGIT_TOKEN_RE.match(tok):
            continue
        expanded = expand_token(tok)
        for word in expanded.split():
            if word not in NOISE_WORDS:
                out.append(word)
    return " ".join(out)
