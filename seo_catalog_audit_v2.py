import csv
import html
import os
import re
import time
from collections import Counter
from pathlib import Path

import requests


# ============================================================
# SEO CATALOG AUDIT V2
# READ ONLY — NO CAMBIOS EN WORDPRESS / WOOCOMMERCE
# ============================================================

WC_BASE_URL = os.getenv(
    "WC_BASE_URL",
    "https://humanitysplagueprod.com/wp-json/wc/v3",
).rstrip("/")

WC_PER_PAGE = 100
HTTP_TIMEOUT = 60
REQUEST_DELAY = 0.15

OUTPUT_FILE = "seo_catalog_audit_v2.csv"
SUMMARY_FILE = "seo_catalog_audit_v2_summary.txt"

# Optional: existing credentials file used by the music importer.
CREDENTIAL_SOURCE = "credentials.txt"

SITE_NAME = "Humanity's Plague Productions"

# Expected SEO title structure:
#
# Artist - Album (Format) | Humanity's Plague Productions
#
# Example:
# Vultur - From The Sardinian Depths (CD) | Humanity's Plague Productions

SEO_TITLE_SEPARATOR = " | "


# ============================================================
# HELPERS
# ============================================================

def clean(value):
    if value is None:
        return ""
    return str(value).strip()


def strip_html(value):
    value = html.unescape(clean(value))
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def word_count(value):
    text = strip_html(value)
    return len(
        re.findall(
            r"\b[\wÀ-ÿ'-]+\b",
            text,
            flags=re.UNICODE,
        )
    )


def extract_assignment(text, names):
    """
    Extract simple assignments such as:

        BASE_URL = "..."
        CONSUMER_KEY = "..."
        CONSUMER_SECRET = "..."

    Supports both single and double quotes.
    """

    for name in names:
        pattern = rf"""
            (?m)
            ^\s*
            {re.escape(name)}
            \s*=\s*
            ["']
            ([^"']+)
            ["']
            \s*$
        """

        match = re.search(
            pattern,
            text,
            flags=re.VERBOSE,
        )

        if match:
            return match.group(1).strip()

    return ""


def normalize_url(url):
    return clean(url).rstrip("/")


def normalize_title_text(value):
    """
    Normalizes whitespace and HTML without changing the actual wording.
    """

    value = strip_html(value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


# ============================================================
# CREDENTIALS
# ============================================================

def load_woocommerce_credentials():

    base_url = WC_BASE_URL

    consumer_key = os.getenv(
        "WC_CONSUMER_KEY",
        "",
    ).strip()

    consumer_secret = os.getenv(
        "WC_CONSUMER_SECRET",
        "",
    ).strip()

    if not (consumer_key and consumer_secret):

        if os.path.exists(CREDENTIAL_SOURCE):

            text = Path(
                CREDENTIAL_SOURCE
            ).read_text(
                encoding="utf-8",
                errors="ignore",
            )

            file_base = extract_assignment(
                text,
                [
                    "BASE_URL",
                    "WC_BASE_URL",
                    "WOOCOMMERCE_BASE_URL",
                ],
            )

            file_key = extract_assignment(
                text,
                [
                    "CONSUMER_KEY",
                    "WC_CONSUMER_KEY",
                    "WOOCOMMERCE_CONSUMER_KEY",
                ],
            )

            file_secret = extract_assignment(
                text,
                [
                    "CONSUMER_SECRET",
                    "WC_CONSUMER_SECRET",
                    "WOOCOMMERCE_CONSUMER_SECRET",
                ],
            )

            if file_base:
                base_url = file_base.rstrip("/")

            if file_key:
                consumer_key = file_key

            if file_secret:
                consumer_secret = file_secret

    if not consumer_key or not consumer_secret:

        raise RuntimeError(
            "No se encontraron credenciales WooCommerce. "
            "Usa WC_CONSUMER_KEY/WC_CONSUMER_SECRET "
            "o credentials.txt."
        )

    return {
        "base_url": base_url,
        "consumer_key": consumer_key,
        "consumer_secret": consumer_secret,
    }


# ============================================================
# HTTP
# ============================================================

session = requests.Session()

session.headers.update(
    {
        "User-Agent": "HumanitysPlagueSEOAudit/2.0",
        "Accept": "application/json",
    }
)


def request_json(
    method,
    url,
    *,
    auth=None,
    params=None,
):

    response = session.request(
        method,
        url,
        auth=auth,
        params=params,
        timeout=HTTP_TIMEOUT,
    )

    if response.status_code >= 400:

        body = response.text[:1000]

        raise RuntimeError(
            f"HTTP {response.status_code} en {url}\n{body}"
        )

    return response.json()


# ============================================================
# WOOCOMMERCE
# ============================================================

def wc_get_products(auth):

    products = []
    page = 1

    print("Cargando productos desde WooCommerce...")
    print()

    while True:

        batch = request_json(
            "GET",
            f"{auth['base_url']}/products",
            auth=(
                auth["consumer_key"],
                auth["consumer_secret"],
            ),
            params={
                "page": page,
                "per_page": WC_PER_PAGE,
                "status": "publish",
            },
        )

        if not isinstance(batch, list):

            raise RuntimeError(
                "WooCommerce no devolvió una lista de productos."
            )

        products.extend(batch)

        print(
            f"  Página {page}: {len(batch)} "
            f"(total {len(products)})"
        )

        if len(batch) < WC_PER_PAGE:
            break

        page += 1

    return products


# ============================================================
# YOAST
# ============================================================

def yoast_get_head(product_url):

    endpoint = (
        "https://humanitysplagueprod.com/"
        "wp-json/yoast/v1/get_head"
    )

    product_url = clean(product_url)

    if not product_url:

        return {
            "status": "NO_URL",
            "title": "",
            "description": "",
            "canonical": "",
            "robots_index": "",
            "robots_follow": "",
            "error": "PRODUCT_HAS_NO_PERMALINK",
        }

    try:

        response = session.get(
            endpoint,
            params={
                "url": product_url,
            },
            timeout=HTTP_TIMEOUT,
        )

    except requests.RequestException as exc:

        return {
            "status": "REQUEST_ERROR",
            "title": "",
            "description": "",
            "canonical": "",
            "robots_index": "",
            "robots_follow": "",
            "error": str(exc),
        }

    # --------------------------------------------------------
    # IMPORTANT:
    #
    # A 404 here does NOT mean:
    # "SEO title missing".
    #
    # It means Yoast could not resolve the supplied URL.
    # --------------------------------------------------------

    if response.status_code >= 400:

        return {
            "status": str(response.status_code),
            "title": "",
            "description": "",
            "canonical": "",
            "robots_index": "",
            "robots_follow": "",
            "error": response.text[:500],
        }

    try:

        data = response.json()

    except ValueError:

        return {
            "status": "INVALID_JSON",
            "title": "",
            "description": "",
            "canonical": "",
            "robots_index": "",
            "robots_follow": "",
            "error": response.text[:500],
        }

    raw = data.get("json") or {}

    robots = raw.get("robots") or {}

    return {
        "status": str(
            data.get(
                "status",
                response.status_code,
            )
        ),
        "title": clean(raw.get("title")),
        "description": clean(raw.get("description")),
        "canonical": clean(raw.get("canonical")),
        "robots_index": clean(robots.get("index")),
        "robots_follow": clean(robots.get("follow")),
        "error": "",
    }


# ============================================================
# PRODUCT PARSING
# ============================================================

def product_has_image(product):

    return bool(
        product.get("images")
    )


def get_image_alt_status(product):

    images = product.get("images") or []

    if not images:

        return {
            "image_count": 0,
            "images_missing_alt": 0,
            "images_with_alt": 0,
        }

    missing = 0
    with_alt = 0

    for image in images:

        alt = clean(
            image.get("alt")
        )

        if alt:
            with_alt += 1
        else:
            missing += 1

    return {
        "image_count": len(images),
        "images_missing_alt": missing,
        "images_with_alt": with_alt,
    }


def parse_product_identity(name):

    name = clean(name)

    # Expected WooCommerce product name:
    #
    # Artist - Album (FORMAT)
    #
    # Example:
    # Vultur - From The Sardinian Depths (CD)

    match = re.match(
        r"^(.*?)\s*-\s*(.*?)\s*\(([^()]*)\)\s*$",
        name,
    )

    if match:

        return (
            clean(match.group(1)),
            clean(match.group(2)),
            clean(match.group(3)),
        )

    # Fallback:
    #
    # Artist - Album

    match = re.match(
        r"^(.*?)\s*-\s*(.*?)$",
        name,
    )

    if match:

        return (
            clean(match.group(1)),
            clean(match.group(2)),
            "",
        )

    return "", name, ""


# ============================================================
# SEO TITLE
# ============================================================

def build_expected_seo_title(
    artist,
    album,
    fmt,
):
    """
    Builds the canonical SEO title structure.

    Expected:

        Artist - Album (Format) | Humanity's Plague Productions

    """

    artist = normalize_title_text(artist)
    album = normalize_title_text(album)
    fmt = normalize_title_text(fmt)

    if not artist or not album:
        return ""

    if fmt:

        base = (
            f"{artist} - {album} ({fmt})"
        )

    else:

        base = (
            f"{artist} - {album}"
        )

    return (
        f"{base}"
        f"{SEO_TITLE_SEPARATOR}"
        f"{SITE_NAME}"
    )


def classify_expected_title(
    actual_title,
    expected_title,
):

    actual_title = clean(actual_title)
    expected_title = clean(expected_title)

    if not actual_title:

        return "MISSING"

    if not expected_title:

        return "NO_EXPECTED_TITLE"

    if actual_title == expected_title:

        return "EXACT"

    return "DIFFERS"


# ============================================================
# LENGTH CLASSIFICATION
# ============================================================

def classify_length(
    value,
    minimum,
    maximum,
):

    length = len(
        clean(value)
    )

    if length == 0:
        return "MISSING"

    if length < minimum:
        return "SHORT"

    if length > maximum:
        return "LONG"

    return "OK"


# ============================================================
# AUDIT FIELDS
# ============================================================

FIELDS = [

    "product_id",
    "sku",
    "name",

    "artist",
    "album",
    "format",

    "slug",
    "permalink",
    "categories",
    "price",

    "description_words",
    "short_description_words",

    "image_count",
    "images_missing_alt",
    "product_has_image",

    "yoast_status",

    "expected_seo_title",
    "seo_title",

    "seo_title_chars",
    "seo_title_status",
    "seo_title_structure",

    "meta_description",
    "meta_description_chars",
    "meta_description_status",

    "canonical",
    "canonical_status",

    "robots_index",
    "robots_follow",

    "seo_error",

    "flags",
]


# ============================================================
# AUDIT PRODUCT
# ============================================================

def audit_product(product):

    product_id = clean(
        product.get("id")
    )

    name = clean(
        product.get("name")
    )

    artist, album, fmt = (
        parse_product_identity(name)
    )

    description = product.get(
        "description",
        "",
    )

    short_description = product.get(
        "short_description",
        "",
    )

    image_info = (
        get_image_alt_status(product)
    )

    categories = (
        product.get("categories")
        or []
    )

    category_names = [

        clean(category.get("name"))

        for category in categories

        if isinstance(
            category,
            dict,
        )
    ]

    permalink = clean(
        product.get("permalink")
    )

    yoast = yoast_get_head(
        permalink
    )

    expected_title = (
        build_expected_seo_title(
            artist,
            album,
            fmt,
        )
    )

    title_status = classify_length(
        yoast["title"],
        30,
        60,
    )

    description_status = (
        classify_length(
            yoast["description"],
            100,
            160,
        )
    )

    title_structure = (
        classify_expected_title(
            yoast["title"],
            expected_title,
        )
    )

    canonical = yoast["canonical"]

    normalized_permalink = (
        normalize_url(permalink)
    )

    normalized_canonical = (
        normalize_url(canonical)
    )

    if not canonical:

        canonical_status = "MISSING"

    elif (
        normalized_canonical
        == normalized_permalink
    ):

        canonical_status = "OK"

    else:

        canonical_status = "DIFFERS"

    flags = []

    # --------------------------------------------------------
    # PRODUCT
    # --------------------------------------------------------

    if not product_has_image(product):

        flags.append(
            "NO_IMAGE"
        )

    if image_info[
        "images_missing_alt"
    ] > 0:

        flags.append(
            "MISSING_IMAGE_ALT"
        )

    if not strip_html(description):

        flags.append(
            "NO_DESCRIPTION"
        )

    if not strip_html(
        short_description
    ):

        flags.append(
            "NO_SHORT_DESCRIPTION"
        )

    if not clean(
        product.get("slug")
    ):

        flags.append(
            "NO_SLUG"
        )

    if not category_names:

        flags.append(
            "NO_CATEGORY"
        )

    # --------------------------------------------------------
    # YOAST REQUEST
    # --------------------------------------------------------

    yoast_status = yoast[
        "status"
    ]

    if yoast_status != "200":

        # Critical difference from V1:
        #
        # We do NOT classify these as:
        # NO_SEO_TITLE
        # NO_META_DESCRIPTION
        #
        # because Yoast was not successfully resolved.

        flags.append(
            "YOAST_UNAVAILABLE"
        )

        if yoast_status == "404":

            flags.append(
                "YOAST_URL_404"
            )

        elif yoast_status in {
            "403",
            "429",
        }:

            flags.append(
                "YOAST_HTTP_BLOCK"
            )

        elif yoast_status in {
            "REQUEST_ERROR",
            "INVALID_JSON",
        }:

            flags.append(
                "YOAST_REQUEST_ERROR"
            )

    else:

        # ----------------------------------------------------
        # SEO TITLE
        # ----------------------------------------------------

        if not yoast["title"]:

            flags.append(
                "NO_SEO_TITLE"
            )

        elif title_status != "OK":

            flags.append(
                f"SEO_TITLE_{title_status}"
            )

        if title_structure == "DIFFERS":

            flags.append(
                "SEO_TITLE_STRUCTURE_DIFFERS"
            )

        elif title_structure == "MISSING":

            flags.append(
                "NO_SEO_TITLE"
            )

        # ----------------------------------------------------
        # META DESCRIPTION
        # ----------------------------------------------------

        if not yoast["description"]:

            flags.append(
                "NO_META_DESCRIPTION"
            )

        elif description_status != "OK":

            flags.append(
                f"META_DESCRIPTION_{description_status}"
            )

        # ----------------------------------------------------
        # CANONICAL
        # ----------------------------------------------------

        if not canonical:

            flags.append(
                "NO_CANONICAL"
            )

        elif canonical_status == "DIFFERS":

            flags.append(
                "CANONICAL_DIFFERS"
            )

        # ----------------------------------------------------
        # ROBOTS
        # ----------------------------------------------------

        if (
            yoast["robots_index"]
            == "noindex"
        ):

            flags.append(
                "NOINDEX"
            )

    return {

        "product_id": product_id,

        "sku": clean(
            product.get("sku")
        ),

        "name": name,

        "artist": artist,

        "album": album,

        "format": fmt,

        "slug": clean(
            product.get("slug")
        ),

        "permalink": permalink,

        "categories": " | ".join(
            category_names
        ),

        "price": clean(
            product.get("price")
        ),

        "description_words": word_count(
            description
        ),

        "short_description_words": (
            word_count(
                short_description
            )
        ),

        "image_count": image_info[
            "image_count"
        ],

        "images_missing_alt": image_info[
            "images_missing_alt"
        ],

        "product_has_image": (
            "YES"
            if product_has_image(product)
            else "NO"
        ),

        "yoast_status": yoast_status,

        "expected_seo_title": expected_title,

        "seo_title": yoast["title"],

        "seo_title_chars": len(
            yoast["title"]
        ),

        "seo_title_status": title_status,

        "seo_title_structure": (
            title_structure
        ),

        "meta_description": (
            yoast["description"]
        ),

        "meta_description_chars": len(
            yoast["description"]
        ),

        "meta_description_status": (
            description_status
        ),

        "canonical": canonical,

        "canonical_status": (
            canonical_status
        ),

        "robots_index": (
            yoast["robots_index"]
        ),

        "robots_follow": (
            yoast["robots_follow"]
        ),

        "seo_error": yoast["error"],

        "flags": " | ".join(flags),
    }


# ============================================================
# CSV
# ============================================================

def write_csv(rows):

    with open(
        OUTPUT_FILE,
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=FIELDS,
            extrasaction="ignore",
        )

        writer.writeheader()

        writer.writerows(rows)


# ============================================================
# SUMMARY
# ============================================================

def write_summary(rows):

    total = len(rows)

    def count(predicate):

        return sum(
            1
            for row in rows
            if predicate(row)
        )

    successful_yoast = [
        row
        for row in rows
        if row["yoast_status"] == "200"
    ]

    title_counter = Counter(
        row["seo_title"]
        for row in successful_yoast
        if row["seo_title"]
    )

    meta_counter = Counter(
        row["meta_description"]
        for row in successful_yoast
        if row["meta_description"]
    )

    duplicate_titles = sum(
        1
        for value in title_counter.values()
        if value > 1
    )

    duplicate_meta = sum(
        1
        for value in meta_counter.values()
        if value > 1
    )

    lines = [

        "HUMANITY'S PLAGUE PRODUCTIONS "
        "— SEO CATALOG AUDIT V2",

        "=" * 60,

        "",

        "MODO: READ ONLY",

        f"PRODUCTOS AUDITADOS: {total}",

        f"YOAST OK: {len(successful_yoast)}",

        f"YOAST NO DISPONIBLE: "
        f"{total - len(successful_yoast)}",

        "",

        "CONTENIDO",

        f"  Sin descripción: "
        f"{count(lambda r: 'NO_DESCRIPTION' in r['flags'])}",

        f"  Sin short description: "
        f"{count(lambda r: 'NO_SHORT_DESCRIPTION' in r['flags'])}",

        "",

        "IMÁGENES",

        f"  Sin imagen: "
        f"{count(lambda r: r['product_has_image'] == 'NO')}",

        f"  Productos con imágenes sin ALT: "
        f"{count(lambda r: 'MISSING_IMAGE_ALT' in r['flags'])}",

        "",

        "YOAST / RESOLUCIÓN",

        f"  Yoast OK: "
        f"{len(successful_yoast)}",

        f"  Yoast HTTP 404: "
        f"{count(lambda r: 'YOAST_URL_404' in r['flags'])}",

        f"  Yoast HTTP 403/429: "
        f"{count(lambda r: 'YOAST_HTTP_BLOCK' in r['flags'])}",

        f"  Otros errores Yoast: "
        f"{count(lambda r: 'YOAST_REQUEST_ERROR' in r['flags'])}",

        "",

        "SEO TITLE",

        f"  Sin SEO title: "
        f"{count(lambda r: 'NO_SEO_TITLE' in r['flags'])}",

        f"  SEO title corto/largo: "
        f"{count(lambda r: r['seo_title_status'] in {'SHORT', 'LONG'})}",

        f"  Estructura correcta: "
        f"{count(lambda r: r['seo_title_structure'] == 'EXACT')}",

        f"  Estructura diferente: "
        f"{count(lambda r: r['seo_title_structure'] == 'DIFFERS')}",

        "",

        "FORMATO SEO TITLE ESPERADO",

        f"  ARTIST - ALBUM (FORMAT) "
        f"| {SITE_NAME}",

        "",

        "META DESCRIPTION",

        f"  Sin meta description: "
        f"{count(lambda r: 'NO_META_DESCRIPTION' in r['flags'])}",

        f"  Meta description corta/larga: "
        f"{count(lambda r: r['meta_description_status'] in {'SHORT', 'LONG'})}",

        "",

        "CANONICAL",

        f"  Sin canonical: "
        f"{count(lambda r: 'NO_CANONICAL' in r['flags'])}",

        f"  Canonical diferente: "
        f"{count(lambda r: 'CANONICAL_DIFFERS' in r['flags'])}",

        "",

        "ROBOTS",

        f"  NOINDEX: "
        f"{count(lambda r: 'NOINDEX' in r['flags'])}",

        "",

        "DUPLICADOS",

        f"  SEO titles duplicados: "
        f"{duplicate_titles}",

        f"  Meta descriptions duplicadas: "
        f"{duplicate_meta}",

        "",

        "DATOS ESTRUCTURALES",

        f"  Sin categoría: "
        f"{count(lambda r: 'NO_CATEGORY' in r['flags'])}",

        f"  Sin slug: "
        f"{count(lambda r: 'NO_SLUG' in r['flags'])}",

        "",

        "IMPORTANTE",

        "  Esta auditoría es READ ONLY.",

        "  No modifica WordPress ni WooCommerce.",

        "  Un error HTTP de Yoast NO se interpreta "
        "como ausencia de SEO.",

        "  Los productos con Yoast no disponible "
        "quedan separados del diagnóstico SEO.",

        "  El SEO title esperado usa la estructura:",

        f"  Artist - Album (Format) "
        f"| {SITE_NAME}",

        "",

        "SALIDAS",

        f"  {OUTPUT_FILE}",

        f"  {SUMMARY_FILE}",
    ]

    Path(
        SUMMARY_FILE
    ).write_text(
        "\n".join(lines),
        encoding="utf-8",
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 60)
    print(" SEO CATALOG AUDIT V2")
    print("=" * 60)

    print()

    print("MODO: READ ONLY")

    print(
        "NO se modificará WooCommerce "
        "ni WordPress."
    )

    print()

    print(
        "SEO TITLE ESPERADO:"
    )

    print(
        f"Artist - Album (Format) "
        f"| {SITE_NAME}"
    )

    print()

    auth = (
        load_woocommerce_credentials()
    )

    print(
        f"Endpoint WooCommerce: "
        f"{auth['base_url']}"
    )

    print()

    products = wc_get_products(
        auth
    )

    print()

    print("=" * 60)
    print("AUDITORÍA")
    print("=" * 60)

    print()

    rows = []

    for number, product in enumerate(
        products,
        start=1,
    ):

        product_id = clean(
            product.get("id")
        )

        name = clean(
            product.get("name")
        )

        print(
            f"[{number}/{len(products)}] "
            f"{product_id}: {name}"
        )

        try:

            row = audit_product(
                product
            )

            rows.append(row)

            if (
                row["yoast_status"]
                != "200"
            ):

                print(
                    f"  YOAST: "
                    f"{row['yoast_status']}"
                )

            else:

                print(
                    f"  YOAST: OK"
                )

                print(
                    f"  TITLE: "
                    f"{row['seo_title_structure']}"
                )

        except Exception as exc:

            print(
                f"  ERROR: {exc}"
            )

            rows.append(
                {
                    "product_id": product_id,
                    "name": name,
                    "seo_error": (
                        f"AUDIT_ERROR: {exc}"
                    ),
                    "flags": "AUDIT_ERROR",
                }
            )

        if REQUEST_DELAY:

            time.sleep(
                REQUEST_DELAY
            )

    write_csv(rows)

    write_summary(rows)

    print()

    print("=" * 60)
    print("AUDITORÍA COMPLETADA")
    print("=" * 60)

    print(
        f"Productos auditados: "
        f"{len(rows)}"
    )

    print(
        f"CSV: {OUTPUT_FILE}"
    )

    print(
        f"Resumen: {SUMMARY_FILE}"
    )

    print()

    print(
        "NO se modificó ningún producto."
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()