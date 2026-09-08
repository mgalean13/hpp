#!/usr/bin/env python3
"""
sync_woocommerce_con_hpp.py
============================

Objetivo:
    Usar "HPP_Catalog.xlsx" como fuente de verdad (canon) de lo que DEBE
    estar publicado en la tienda WooCommerce, y borrar de WooCommerce
    todos los productos que aparecen en "products_export.csv" (lo que
    hoy está publicado) pero cuyo SKU ya NO existe en el catálogo HPP.

Cómo funciona:
    1. Lee todas las hojas de HPP_Catalog.xlsx (ignora la hoja
       "Test Card Numbers") y arma el conjunto de SKUs "canon".
       Si una celda SKU trae varios SKUs separados por coma
       (ej. "SMS176, Ato-113") los separa y agrega cada uno.
    2. Lee products_export.csv (lo que hay en WooCommerce ahora mismo).
    3. Para cada producto del export, compara su SKU contra el conjunto
       canon (comparación insensible a mayúsculas y a espacios).
       Si NINGUNO de sus SKUs está en el canon -> se marca para borrar.
    4. Guarda SIEMPRE un reporte CSV con los candidatos a borrar
       (productos_a_borrar.csv) ANTES de borrar nada.
    5. Solo si corres el script con --execute, se conecta a la API REST
       de WooCommerce y borra esos productos (por defecto los manda a la
       papelera; con --force los borra definitivamente).

Requisitos:
    pip install requests openpyxl

Configuración:
    Completa las variables WC_URL, WC_CONSUMER_KEY y WC_CONSUMER_SECRET
    más abajo, o pásalas por variables de entorno del mismo nombre.
    Las credenciales se generan en:
    WooCommerce > Ajustes > Avanzado > REST API > Agregar clave
    (dale permisos de Lectura/Escritura).

Uso:
    # 1) Solo generar el reporte de qué se borraría (no toca la tienda)
    python3 sync_woocommerce_con_hpp.py

    # 2) Borrar de verdad (manda a la papelera) los productos del reporte
    python3 sync_woocommerce_con_hpp.py --execute

    # 3) Borrar de verdad y de forma DEFINITIVA (sin pasar por la papelera)
    python3 sync_woocommerce_con_hpp.py --execute --force
"""

import argparse
import csv
import os
import re
import sys
import time

import openpyxl

try:
    import requests
except ImportError:
    requests = None


# ----------------------------------------------------------------------
# CONFIGURACIÓN — completa esto con los datos de tu tienda
# ----------------------------------------------------------------------
HPP_CATALOG_PATH = "HPP_Catalog_Importer.xlsx"
PRODUCTS_EXPORT_PATH = "products_export.csv"
REPORT_PATH = "productos_a_borrar.csv"

WC_URL = os.environ.get("WC_URL", "https://humanitysplagueprod.com")
WC_CONSUMER_KEY = os.environ.get("WC_CONSUMER_KEY", "ck_bd0a4100516c5429b0648c835919d291c7e568b3")
WC_CONSUMER_SECRET = os.environ.get("WC_CONSUMER_SECRET", "cs_01dd699e0277d29a6c491f7c1137515b2c1c2ad2")

# Hojas del Excel que NO son catálogo real y deben ignorarse
HOJAS_A_IGNORAR = {"test card numbers"}

# Tamaño de lote para el endpoint /products/batch de WooCommerce
BATCH_SIZE = 100
# ----------------------------------------------------------------------


def normalizar_sku(sku: str) -> str:
    """Quita espacios y pasa a mayúsculas, para comparar SKUs de forma robusta."""
    return re.sub(r"\s+", "", str(sku)).upper()


def cargar_skus_canon(path: str) -> set:
    """Lee todas las hojas del HPP_Catalog.xlsx y devuelve el set de SKUs válidos."""
    wb = openpyxl.load_workbook(path, read_only=True)
    skus = set()

    for ws in wb.worksheets:
        if ws.title.strip().lower() in HOJAS_A_IGNORAR:
            continue

        header = None
        for row in ws.iter_rows(values_only=True):
            if header is None:
                header = [
                    (c or "").strip().upper() if isinstance(c, str) else c
                    for c in row
                ]
                continue
            data = dict(zip(header, row))
            sku_val = data.get("SKU")
            if sku_val and str(sku_val).strip():
                # una celda puede traer varios SKUs separados por coma
                for parte in re.split(r",", str(sku_val)):
                    parte = parte.strip()
                    if parte:
                        skus.add(normalizar_sku(parte))

    return skus


def cargar_productos_export(path: str) -> list:
    """Lee products_export.csv y devuelve la lista de filas (dict)."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader)


def calcular_candidatos_a_borrar(productos_export: list, skus_canon: set) -> list:
    """
    Devuelve la lista de productos del export cuyo SKU NO está en el catálogo canon.
    Productos sin SKU se dejan fuera del análisis por seguridad (no se tocan).
    """
    candidatos = []
    sin_sku = 0

    for row in productos_export:
        sku_raw = (row.get("sku") or "").strip()
        if not sku_raw:
            sin_sku += 1
            continue

        tokens = [normalizar_sku(p) for p in re.split(r",", sku_raw) if p.strip()]
        if not any(t in skus_canon for t in tokens):
            candidatos.append(row)

    if sin_sku:
        print(f"Aviso: {sin_sku} productos del export no tienen SKU y se excluyeron del análisis (revisar a mano).")

    return candidatos


def guardar_reporte(candidatos: list, path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "name", "sku", "status"])
        writer.writeheader()
        for row in candidatos:
            writer.writerow({
                "id": row.get("id", ""),
                "name": row.get("name", ""),
                "sku": row.get("sku", ""),
                "status": row.get("status", ""),
            })


def borrar_en_woocommerce(ids: list, force: bool) -> None:
    """Borra productos en WooCommerce usando el endpoint /products/batch (delete)."""
    if requests is None:
        sys.exit("Falta instalar la librería 'requests'. Corre: pip install requests")

    if "tu-tienda.com" in WC_URL or "ck_xxx" in WC_CONSUMER_KEY:
        sys.exit(
            "Debes completar WC_URL, WC_CONSUMER_KEY y WC_CONSUMER_SECRET "
            "en la sección de configuración del script (o como variables de entorno) "
            "antes de ejecutar con --execute."
        )

    endpoint = f"{WC_URL.rstrip('/')}/wp-json/wc/v3/products/batch"
    auth = (WC_CONSUMER_KEY, WC_CONSUMER_SECRET)

    total = len(ids)
    borrados_ok = []
    errores = []

    for i in range(0, total, BATCH_SIZE):
        lote = ids[i:i + BATCH_SIZE]
        payload = {"delete": lote}
        params = {"force": "true"} if force else {}

        print(f"Borrando lote {i // BATCH_SIZE + 1} ({len(lote)} productos)...")
        resp = requests.post(endpoint, json=payload, params=params, auth=auth, timeout=60)

        if resp.status_code not in (200, 201):
            print(f"  ERROR HTTP {resp.status_code}: {resp.text[:500]}")
            errores.extend(lote)
            continue

        data = resp.json()
        for item in data.get("delete", []):
            pid = item.get("id")
            if "error" in item:
                print(f"  Error borrando id {pid}: {item['error'].get('message')}")
                errores.append(pid)
            else:
                borrados_ok.append(pid)

        time.sleep(0.5)  # pequeño respiro para no saturar la API

    print("\n--- Resumen ---")
    print(f"Borrados correctamente: {len(borrados_ok)}")
    print(f"Con error: {len(errores)}")
    if errores:
        print(f"IDs con error: {errores}")


def main():
    parser = argparse.ArgumentParser(description="Sincroniza WooCommerce contra el catálogo HPP.")
    parser.add_argument("--execute", action="store_true",
                         help="Además de generar el reporte, borra de verdad los productos en WooCommerce.")
    parser.add_argument("--force", action="store_true",
                         help="Junto con --execute, borra de forma DEFINITIVA (sin pasar por la papelera).")
    parser.add_argument("--hpp", default=HPP_CATALOG_PATH, help="Ruta al archivo HPP_Catalog.xlsx")
    parser.add_argument("--export", default=PRODUCTS_EXPORT_PATH, help="Ruta al archivo products_export.csv")
    args = parser.parse_args()

    print("Leyendo catálogo HPP (fuente de verdad)...")
    skus_canon = cargar_skus_canon(args.hpp)
    print(f"  SKUs canon encontrados: {len(skus_canon)}")

    print("Leyendo export de WooCommerce...")
    productos_export = cargar_productos_export(args.export)
    print(f"  Productos en WooCommerce (export): {len(productos_export)}")

    candidatos = calcular_candidatos_a_borrar(productos_export, skus_canon)
    print(f"\nProductos a borrar (SKU no está en el catálogo HPP): {len(candidatos)}")

    guardar_reporte(candidatos, REPORT_PATH)
    print(f"Reporte guardado en: {REPORT_PATH}")

    if not args.execute:
        print("\nModo simulación (dry-run). No se borró nada en WooCommerce.")
        print("Revisa el reporte y, si está correcto, vuelve a correr con --execute.")
        return

    ids = [int(row["id"]) for row in candidatos if row.get("id")]
    if not ids:
        print("No hay productos para borrar.")
        return

    confirmacion = input(
        f"\nVas a borrar {len(ids)} productos en {WC_URL} "
        f"({'DEFINITIVO' if args.force else 'a la papelera'}). "
        f"Escribe 'BORRAR' para confirmar: "
    )
    if confirmacion.strip() != "BORRAR":
        print("Cancelado por el usuario.")
        return

    borrar_en_woocommerce(ids, force=args.force)


if __name__ == "__main__":
    main()
