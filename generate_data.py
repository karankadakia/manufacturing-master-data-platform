"""
Manufacturing Master Data - Synthetic Data Generator
Generates realistic SAP-like tables for hands-on learning projects.
Run: python data/generate_data.py
Outputs CSV files to data/raw/
"""

import csv
import random
import os
from datetime import datetime, timedelta

random.seed(42)
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "raw")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ── helpers ──────────────────────────────────────────────────────────────────

def rand_date(start="2020-01-01", end="2024-12-31"):
    s = datetime.strptime(start, "%Y-%m-%d")
    e = datetime.strptime(end, "%Y-%m-%d")
    return (s + timedelta(days=random.randint(0, (e - s).days))).strftime("%Y-%m-%d")

def write_csv(filename, rows, fieldnames):
    path = os.path.join(OUTPUT_DIR, filename)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    print(f"  Written: {filename}  ({len(rows)} rows)")

# ── 1. material_master ────────────────────────────────────────────────────────

MATERIAL_TYPES = ["ROH", "HALB", "FERT", "VERP", "HIBE"]  # raw, semi, finished, packaging, MRO
UOM_LIST       = ["EA", "KG", "L", "M", "PC", "SET", "BOX"]
LIFECYCLES     = ["PROTOTYPE", "ACTIVE", "OBSOLETE", "BLOCKED", "DISCONTINUED"]
PLANTS         = ["US01", "US02", "DE01", "MX01"]
PROC_TYPES     = ["E", "F", "X"]   # external, in-house, both

materials = []
for i in range(1, 201):
    mat_id  = f"MAT{i:05d}"
    mtype   = random.choice(MATERIAL_TYPES)
    status  = random.choices(LIFECYCLES, weights=[5, 70, 10, 8, 7])[0]
    # intentional quality issues in ~15% of records
    desc    = "" if random.random() < 0.05 else f"Material description for {mat_id}"
    uom     = random.choice(UOM_LIST)
    bad_uom = random.random() < 0.04          # invalid UOM flag
    lead    = None if random.random() < 0.08 else random.randint(1, 90)
    lot_sz  = random.choice([1, 10, 50, 100, 500])
    safety  = random.randint(0, 200)
    reorder = None if random.random() < 0.06 else random.randint(0, 100)
    cost    = round(random.uniform(0.5, 5000), 2)
    proc    = random.choice(PROC_TYPES)
    planner = None if random.random() < 0.07 else f"P{random.randint(100,199)}"
    hazmat  = random.choice(["Y", "N", "N", "N"])
    coo     = random.choice(["US", "DE", "CN", "MX", "IN", ""])
    batch   = random.choice(["X", "", ""])

    materials.append({
        "material_id": mat_id,
        "material_type": mtype,
        "description": desc,
        "uom": "INVALID" if bad_uom else uom,
        "lifecycle_status": status,
        "lead_time_days": lead,
        "lot_size": lot_sz,
        "safety_stock": safety,
        "reorder_point": reorder,
        "standard_cost_usd": cost,
        "procurement_type": proc,
        "mrp_controller": planner,
        "hazmat_flag": hazmat,
        "country_of_origin": coo,
        "batch_tracking": batch,
        "created_date": rand_date("2015-01-01", "2023-12-31"),
        "last_changed_date": rand_date("2023-01-01", "2024-12-31"),
    })

write_csv("material_master.csv", materials,
    ["material_id","material_type","description","uom","lifecycle_status",
     "lead_time_days","lot_size","safety_stock","reorder_point","standard_cost_usd",
     "procurement_type","mrp_controller","hazmat_flag","country_of_origin",
     "batch_tracking","created_date","last_changed_date"])

# ── 2. plant_material_view ────────────────────────────────────────────────────

plant_views = []
assigned_materials = set()
for mat in materials:
    n_plants = random.choices([0, 1, 2, 3, 4], weights=[8, 40, 30, 15, 7])[0]
    for plant in random.sample(PLANTS, min(n_plants, len(PLANTS))):
        assigned_materials.add(mat["material_id"])
        plant_views.append({
            "material_id": mat["material_id"],
            "plant": plant,
            "storage_location": f"SL{random.randint(1,5):02d}",
            "special_procurement": random.choice(["", "50", "30", ""]),
            "plant_specific_lead_time": random.choice([None, None, random.randint(1, 60)]),
            "plant_status": random.choice(["ACTIVE","ACTIVE","ACTIVE","BLOCKED"]),
        })

write_csv("plant_material_view.csv", plant_views,
    ["material_id","plant","storage_location","special_procurement",
     "plant_specific_lead_time","plant_status"])

# ── 3. bom_header + bom_item ──────────────────────────────────────────────────

FERT_mats = [m["material_id"] for m in materials if m["material_type"] == "FERT"]
COMP_mats  = [m["material_id"] for m in materials if m["material_type"] in ("ROH", "HALB")]

bom_headers = []
bom_items   = []
bom_id = 1000

for parent in random.sample(FERT_mats, min(40, len(FERT_mats))):
    for bom_usage in ["1", "3"]:   # 1=production, 3=universal
        bom_no = f"BOM{bom_id}"
        bom_id += 1
        valid_from = rand_date("2020-01-01", "2022-12-31")
        valid_to   = rand_date("2025-01-01", "2027-12-31") if random.random() > 0.1 else "9999-12-31"
        bom_status = random.choices(["ACTIVE","ACTIVE","ACTIVE","OBSOLETE","BLOCKED"], weights=[70,0,0,15,15])[0]
        bom_headers.append({
            "bom_number": bom_no,
            "parent_material": parent,
            "bom_usage": bom_usage,
            "valid_from": valid_from,
            "valid_to": valid_to,
            "bom_status": bom_status,
            "plant": random.choice(PLANTS),
            "base_quantity": 1,
            "base_uom": "EA",
        })
        n_comps = random.randint(2, 10)
        for j, comp in enumerate(random.sample(COMP_mats, min(n_comps, len(COMP_mats))), 1):
            qty = round(random.uniform(0.1, 50), 3)
            scrap = round(random.uniform(0, 0.05), 3)
            is_phantom = random.random() < 0.05
            bom_items.append({
                "bom_number": bom_no,
                "item_number": j * 10,
                "component_material": comp,
                "quantity": qty,
                "uom": "EA",
                "scrap_factor": scrap,
                "is_phantom": "Y" if is_phantom else "N",
                "valid_from": valid_from,
                "valid_to": valid_to,
            })

write_csv("bom_header.csv", bom_headers,
    ["bom_number","parent_material","bom_usage","valid_from","valid_to",
     "bom_status","plant","base_quantity","base_uom"])

write_csv("bom_item.csv", bom_items,
    ["bom_number","item_number","component_material","quantity","uom",
     "scrap_factor","is_phantom","valid_from","valid_to"])

# ── 4. supplier_master ────────────────────────────────────────────────────────

PAYMENT_TERMS = ["NET30", "NET60", "NET15", "2/10NET30", None]
RISK_CATS     = ["LOW", "MEDIUM", "HIGH"]
COUNTRIES     = ["US", "DE", "CN", "MX", "IN", "JP"]

suppliers = []
# some intentional duplicates with slight variations
base_names = [f"Acme Corp {i}" for i in range(1, 71)] + \
             [f"Global Supplies {i}" for i in range(1, 31)]

for idx, name in enumerate(base_names, 1):
    sup_id = f"SUP{idx:04d}"
    # duplicate: same company different record
    if idx % 20 == 0:
        name = name.replace("Corp", "Corporation").replace("Supplies", "Supply")
    suppliers.append({
        "supplier_id": sup_id,
        "supplier_name": name,
        "supplier_site": random.choice(["HQ", "PLANT1", "PLANT2"]),
        "country": random.choice(COUNTRIES),
        "tax_id": None if random.random() < 0.06 else f"TX{random.randint(100000,999999)}",
        "payment_terms": random.choice(PAYMENT_TERMS),
        "currency": "USD",
        "risk_category": random.choice(RISK_CATS),
        "certified": random.choice(["Y", "Y", "N"]),
        "single_source_flag": random.choice(["Y", "N", "N", "N"]),
        "active": random.choice(["Y", "Y", "Y", "N"]),
        "created_date": rand_date("2010-01-01", "2022-12-31"),
    })

write_csv("supplier_master.csv", suppliers,
    ["supplier_id","supplier_name","supplier_site","country","tax_id",
     "payment_terms","currency","risk_category","certified",
     "single_source_flag","active","created_date"])

# ── 5. purchase_order_header + line ──────────────────────────────────────────

ACTIVE_SUPS  = [s["supplier_id"] for s in suppliers if s["active"] == "Y"]
ACTIVE_MATS  = [m["material_id"] for m in materials if m["lifecycle_status"] == "ACTIVE"]

po_headers = []
po_lines   = []
for po_num in range(4500001, 4500201):
    sup = random.choice(ACTIVE_SUPS)
    po_date = rand_date("2023-01-01", "2024-06-30")
    po_headers.append({
        "po_number": po_num,
        "supplier_id": sup,
        "po_date": po_date,
        "plant": random.choice(PLANTS),
        "currency": "USD",
        "status": random.choice(["OPEN","CLOSED","CLOSED","CANCELLED"]),
    })
    n_lines = random.randint(1, 5)
    for ln in range(1, n_lines + 1):
        mat = random.choice(ACTIVE_MATS)
        qty = random.randint(10, 500)
        price = round(random.uniform(1, 2000), 2)
        delivery_days = random.randint(5, 60)
        promised = (datetime.strptime(po_date, "%Y-%m-%d") +
                    timedelta(days=delivery_days)).strftime("%Y-%m-%d")
        # 25% chance of late delivery, 5% not delivered
        r = random.random()
        if r < 0.05:
            actual = None
        elif r < 0.30:
            actual = (datetime.strptime(promised, "%Y-%m-%d") +
                      timedelta(days=random.randint(1, 14))).strftime("%Y-%m-%d")
        else:
            actual = (datetime.strptime(promised, "%Y-%m-%d") -
                      timedelta(days=random.randint(0, 3))).strftime("%Y-%m-%d")

        po_lines.append({
            "po_number": po_num,
            "line_number": ln * 10,
            "material_id": mat,
            "quantity_ordered": qty,
            "quantity_received": 0 if actual is None else qty,
            "unit_price_usd": price,
            "promised_delivery_date": promised,
            "actual_delivery_date": actual,
            "uom": "EA",
        })

write_csv("purchase_order_header.csv", po_headers,
    ["po_number","supplier_id","po_date","plant","currency","status"])

write_csv("purchase_order_line.csv", po_lines,
    ["po_number","line_number","material_id","quantity_ordered","quantity_received",
     "unit_price_usd","promised_delivery_date","actual_delivery_date","uom"])

# ── 6. inventory_balance ──────────────────────────────────────────────────────

inv_rows = []
used_mats = list(assigned_materials)
for mat_id in random.sample(used_mats, min(160, len(used_mats))):
    plant = random.choice(PLANTS)
    qty_on_hand = round(random.uniform(0, 5000), 2)
    blocked_qty = round(qty_on_hand * random.uniform(0, 0.15), 2)
    qc_qty      = round(qty_on_hand * random.uniform(0, 0.10), 2)
    avail_qty   = max(0, qty_on_hand - blocked_qty - qc_qty)
    unit_val    = round(random.uniform(0.5, 3000), 2)
    days_no_mov = random.choices(
        [random.randint(0, 30),
         random.randint(31, 180),
         random.randint(181, 730)],
        weights=[60, 25, 15]
    )[0]
    last_mov = (datetime.today() - timedelta(days=days_no_mov)).strftime("%Y-%m-%d")
    inv_rows.append({
        "material_id": mat_id,
        "plant": plant,
        "storage_location": f"SL{random.randint(1,5):02d}",
        "qty_on_hand": qty_on_hand,
        "qty_available": avail_qty,
        "qty_blocked": blocked_qty,
        "qty_qc_hold": qc_qty,
        "unit_value_usd": unit_val,
        "total_value_usd": round(qty_on_hand * unit_val, 2),
        "last_movement_date": last_mov,
        "snapshot_date": "2024-12-31",
    })

write_csv("inventory_balance.csv", inv_rows,
    ["material_id","plant","storage_location","qty_on_hand","qty_available",
     "qty_blocked","qty_qc_hold","unit_value_usd","total_value_usd",
     "last_movement_date","snapshot_date"])

print("\nAll raw data files generated in data/raw/")
