import os
import requests
import pandas as pd
from dotenv import load_dotenv

# Cargar variables del archivo .env
load_dotenv()

url = os.getenv("WOOCOMMERCE_URL")
consumer_key = os.getenv("WC_CONSUMER_KEY")
consumer_secret = os.getenv("WC_CONSUMER_SECRET")

endpoint = f"{url}/wp-json/wc/v3/products"

all_products = []
page = 1

print("========================================")
print(" EXPORTADOR DE PRODUCTOS WOOCOMMERCE")
print("========================================")
print()

while True:

    print(f"Descargando página {page}...")

    response = requests.get(
        endpoint,
        auth=(consumer_key, consumer_secret),
        params={
            "per_page": 100,
            "page": page
        },
        timeout=60
    )

    response.raise_for_status()

    products = response.json()

    if not products:
        break

    all_products.extend(products)

    print(f"  Productos obtenidos: {len(products)}")

    page += 1


print()
print("========================================")
print(" DESCARGA COMPLETADA")
print("========================================")
print()
print(f"Total de productos: {len(all_products)}")
print()


rows = []

for product in all_products:

    images = product.get("images", [])

    image_urls = []

    for image in images:
        image_urls.append(image.get("src", ""))

    rows.append({
        "id": product.get("id", ""),
        "name": product.get("name", ""),
        "sku": product.get("sku", ""),
        "type": product.get("type", ""),
        "status": product.get("status", ""),
        "description": product.get("description", ""),
        "short_description": product.get("short_description", ""),
        "categories": ", ".join(
            category.get("name", "")
            for category in product.get("categories", [])
        ),
        "tags": ", ".join(
            tag.get("name", "")
            for tag in product.get("tags", [])
        ),
        "images": " | ".join(image_urls),
        "permalink": product.get("permalink", "")
    })


df = pd.DataFrame(rows)

output_file = "products_export.csv"

df.to_csv(
    output_file,
    index=False,
    encoding="utf-8-sig"
)

print(f"Archivo creado: {output_file}")
print()
print("El catálogo NO ha sido modificado.")