"""
PROJECT 2: Master Data Quality Scorecard
=========================================
What this teaches:
- SQL data quality rules pattern (the foundation of any MDM governance role)
- Exception table design - how to categorize, store, and report data errors
- Scoring by domain, plant, and severity
- Window functions for ranking and deduplication

This is the #1 thing manufacturing MDM hiring managers look for.
Every bad material, duplicate supplier, or missing field has a
real cost: procurement errors, MRP failures, wrong shipments, cost blowouts.

Run: python sql/project2_dq_scorecard.py
"""

import sqlite3
import csv
import os
from collections import defaultdict

RAW_DIR     = os.path.join(os.path.dirname(r"C:\Users\kadak\DE projects\data\raw"), "..", "data", "raw")
EXCEPT_DIR  = os.path.join(os.path.dirname(r"C:\Users\kadak\DE projects\data\exceptions"), "..", "data", "exceptions")
os.makedirs(EXCEPT_DIR, exist_ok=True)

def load_csv(conn, table, filepath):
    with open(filepath, newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        if not rows:
            return
        cols = rows[0].keys()
        conn.execute(f"DROP TABLE IF EXISTS {table}")
        col_defs = ", ".join(f'"{c}" TEXT' for c in cols)
        conn.execute(f"CREATE TABLE {table} ({col_defs})")
        placeholders = ", ".join("?" for _ in cols)
        conn.executemany(
            f"INSERT INTO {table} VALUES ({placeholders})",
            [tuple(r[c] for c in cols) for r in rows]
        )
    conn.commit()

conn = sqlite3.connect(":memory:")
conn.row_factory = sqlite3.Row

print("Loading data...")
for table, fname in [
    ("material_master",      "material_master.csv"),
    ("plant_material_view",  "plant_material_view.csv"),
    ("bom_header",           "bom_header.csv"),
    ("bom_item",             "bom_item.csv"),
    ("supplier_master",      "supplier_master.csv"),
    ("purchase_order_header","purchase_order_header.csv"),
    ("purchase_order_line",  "purchase_order_line.csv"),
    ("inventory_balance",    "inventory_balance.csv"),
]:
    load_csv(conn, table, os.path.join(RAW_DIR, fname))
    print(f"  Loaded {table}")

# ─────────────────────────────────────────────────────────────────────────────
# DATA QUALITY RULES
# Each rule returns rows with: entity_id, rule_id, rule_name, severity, detail
# Severity: CRITICAL (blocks operations), HIGH (causes errors), MEDIUM (risk)
# ─────────────────────────────────────────────────────────────────────────────

DQ_RULES = {

    # ── MATERIAL MASTER ──────────────────────────────────────────────────────

    "MAT-001": {
        "name": "Missing material description",
        "severity": "HIGH",
        "domain": "Material",
        "business_impact": "Buyers cannot identify the material; procurement errors likely.",
        "sql": """
            SELECT material_id AS entity_id,
                   'MAT-001' AS rule_id,
                   'Missing material description' AS rule_name,
                   'HIGH' AS severity,
                   'Description is blank or null' AS detail
            FROM material_master
            WHERE description IS NULL OR TRIM(description) = ''
        """
    },

    "MAT-002": {
        "name": "Invalid unit of measure",
        "severity": "CRITICAL",
        "domain": "Material",
        "business_impact": "Incorrect UOM breaks purchase orders, goods receipts, and inventory valuations.",
        "sql": """
            SELECT material_id AS entity_id,
                   'MAT-002' AS rule_id,
                   'Invalid unit of measure' AS rule_name,
                   'CRITICAL' AS severity,
                   'UOM value: ' || uom AS detail
            FROM material_master
            WHERE uom NOT IN ('EA','KG','L','M','PC','SET','BOX','G','ML','FT','LB','TON','ROL')
        """
    },

    "MAT-003": {
        "name": "Active material missing MRP controller",
        "severity": "HIGH",
        "domain": "Material",
        "business_impact": "MRP runs with no planner assigned; shortage goes unnoticed.",
        "sql": """
            SELECT material_id AS entity_id,
                   'MAT-003' AS rule_id,
                   'Active material missing MRP controller' AS rule_name,
                   'HIGH' AS severity,
                   'Lifecycle: ' || lifecycle_status || ', MRP controller: NULL' AS detail
            FROM material_master
            WHERE lifecycle_status = 'ACTIVE'
              AND (mrp_controller IS NULL OR TRIM(mrp_controller) = '')
        """
    },

    "MAT-004": {
        "name": "Active material missing lead time",
        "severity": "HIGH",
        "domain": "Material",
        "business_impact": "MRP cannot schedule procurement; stockouts likely.",
        "sql": """
            SELECT material_id AS entity_id,
                   'MAT-004' AS rule_id,
                   'Active material missing lead time' AS rule_name,
                   'HIGH' AS severity,
                   'Procurement type: ' || procurement_type AS detail
            FROM material_master
            WHERE lifecycle_status = 'ACTIVE'
              AND (lead_time_days IS NULL OR lead_time_days = '')
        """
    },

    "MAT-005": {
        "name": "Active material with no plant view",
        "severity": "CRITICAL",
        "domain": "Material",
        "business_impact": "Material cannot be used in any plant; invisible to MRP and procurement.",
        "sql": """
            SELECT m.material_id AS entity_id,
                   'MAT-005' AS rule_id,
                   'Active material with no plant view' AS rule_name,
                   'CRITICAL' AS severity,
                   'Material type: ' || m.material_type AS detail
            FROM material_master m
            LEFT JOIN plant_material_view pv ON pv.material_id = m.material_id
            WHERE m.lifecycle_status = 'ACTIVE'
              AND pv.material_id IS NULL
        """
    },

    "MAT-006": {
        "name": "Reorder point set without lead time",
        "severity": "MEDIUM",
        "domain": "Material",
        "business_impact": "Reorder trigger fires but replenishment time is unknown; wrong order timing.",
        "sql": """
            SELECT material_id AS entity_id,
                   'MAT-006' AS rule_id,
                   'Reorder point set without lead time' AS rule_name,
                   'MEDIUM' AS severity,
                   'Reorder point: ' || reorder_point AS detail
            FROM material_master
            WHERE (reorder_point IS NOT NULL AND reorder_point != '' AND CAST(reorder_point AS REAL) > 0)
              AND (lead_time_days IS NULL OR lead_time_days = '')
        """
    },

    # ── BOM ──────────────────────────────────────────────────────────────────

    "BOM-001": {
        "name": "Active BOM using obsolete/blocked component",
        "severity": "CRITICAL",
        "domain": "BOM",
        "business_impact": "Production orders will reference unavailable parts; line stoppages likely.",
        "sql": """
            SELECT bi.component_material AS entity_id,
                   'BOM-001' AS rule_id,
                   'Active BOM using obsolete/blocked component' AS rule_name,
                   'CRITICAL' AS severity,
                   'BOM: ' || bh.bom_number || ', Parent: ' || bh.parent_material
                   || ', Component status: ' || mm.lifecycle_status AS detail
            FROM bom_header bh
            JOIN bom_item bi ON bi.bom_number = bh.bom_number
            JOIN material_master mm ON mm.material_id = bi.component_material
            WHERE bh.bom_status = 'ACTIVE'
              AND mm.lifecycle_status IN ('OBSOLETE','BLOCKED','DISCONTINUED')
        """
    },

    "BOM-002": {
        "name": "BOM component references non-existent material",
        "severity": "CRITICAL",
        "domain": "BOM",
        "business_impact": "Referential integrity failure; MRP explosion produces phantom demand.",
        "sql": """
            SELECT bi.component_material AS entity_id,
                   'BOM-002' AS rule_id,
                   'BOM component references non-existent material' AS rule_name,
                   'CRITICAL' AS severity,
                   'BOM: ' || bi.bom_number AS detail
            FROM bom_item bi
            LEFT JOIN material_master mm ON mm.material_id = bi.component_material
            WHERE mm.material_id IS NULL
        """
    },

    # ── SUPPLIER ─────────────────────────────────────────────────────────────

    "SUP-001": {
        "name": "Active supplier missing payment terms",
        "severity": "HIGH",
        "domain": "Supplier",
        "business_impact": "AP cannot process invoices; payment delays, supplier relationship risk.",
        "sql": """
            SELECT supplier_id AS entity_id,
                   'SUP-001' AS rule_id,
                   'Active supplier missing payment terms' AS rule_name,
                   'HIGH' AS severity,
                   supplier_name AS detail
            FROM supplier_master
            WHERE active = 'Y'
              AND (payment_terms IS NULL OR TRIM(payment_terms) = '')
        """
    },

    "SUP-002": {
        "name": "Supplier missing tax ID",
        "severity": "HIGH",
        "domain": "Supplier",
        "business_impact": "Tax reporting non-compliance; potential regulatory fines.",
        "sql": """
            SELECT supplier_id AS entity_id,
                   'SUP-002' AS rule_id,
                   'Supplier missing tax ID' AS rule_name,
                   'HIGH' AS severity,
                   supplier_name AS detail
            FROM supplier_master
            WHERE active = 'Y'
              AND (tax_id IS NULL OR TRIM(tax_id) = '')
        """
    },

    "SUP-003": {
        "name": "Single-source supplier with high risk category",
        "severity": "HIGH",
        "domain": "Supplier",
        "business_impact": "Single point of failure in supply chain; any disruption halts production.",
        "sql": """
            SELECT supplier_id AS entity_id,
                   'SUP-003' AS rule_id,
                   'Single-source high-risk supplier' AS rule_name,
                   'HIGH' AS severity,
                   supplier_name || ' | Risk: ' || risk_category || ' | Country: ' || country AS detail
            FROM supplier_master
            WHERE single_source_flag = 'Y'
              AND risk_category = 'HIGH'
              AND active = 'Y'
        """
    },

    # ── INVENTORY ────────────────────────────────────────────────────────────

    "INV-001": {
        "name": "High-value inventory with no movement > 365 days",
        "severity": "HIGH",
        "domain": "Inventory",
        "business_impact": "Excess/obsolete inventory; cash tied up; potential write-off.",
        "sql": """
            SELECT ib.material_id AS entity_id,
                   'INV-001' AS rule_id,
                   'High-value inventory no movement 365+ days' AS rule_name,
                   'HIGH' AS severity,
                   'Plant: ' || ib.plant || ', Value: $' ||
                   ROUND(CAST(ib.total_value_usd AS REAL), 0) ||
                   ', Last movement: ' || ib.last_movement_date AS detail
            FROM inventory_balance ib
            WHERE JULIANDAY('2024-12-31') - JULIANDAY(ib.last_movement_date) > 365
              AND CAST(ib.total_value_usd AS REAL) > 10000
        """
    },

    "INV-002": {
        "name": "Inventory record for non-existent material",
        "severity": "CRITICAL",
        "domain": "Inventory",
        "business_impact": "Ghost inventory; inflated balance sheet; audit risk.",
        "sql": """
            SELECT ib.material_id AS entity_id,
                   'INV-002' AS rule_id,
                   'Inventory exists for non-existent material' AS rule_name,
                   'CRITICAL' AS severity,
                   'Plant: ' || ib.plant || ', Qty: ' || ib.qty_on_hand AS detail
            FROM inventory_balance ib
            LEFT JOIN material_master mm ON mm.material_id = ib.material_id
            WHERE mm.material_id IS NULL
        """
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# Execute all rules and collect exceptions
# ─────────────────────────────────────────────────────────────────────────────

print("\n=== Running Data Quality Rules ===\n")
all_exceptions = []
summary = defaultdict(lambda: {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "total": 0})

for rule_id, rule in DQ_RULES.items():
    rows = conn.execute(rule["sql"]).fetchall()
    count = len(rows)
    sev = rule["severity"]
    domain = rule["domain"]
    summary[domain][sev] += count
    summary[domain]["total"] += count
    print(f"  [{sev:8s}] {rule_id}: {rule['name']}")
    print(f"            Violations: {count}")
    if count > 0:
        print(f"            Impact: {rule['business_impact']}")
    print()
    for r in rows:
        all_exceptions.append({
            "rule_id": r["rule_id"],
            "rule_name": r["rule_name"],
            "severity": r["severity"],
            "domain": domain,
            "entity_id": r["entity_id"],
            "detail": r["detail"],
            "business_impact": rule["business_impact"],
        })

# Write exception file
exc_path = os.path.join(EXCEPT_DIR, "master_data_exceptions.csv")
if all_exceptions:
    with open(exc_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=all_exceptions[0].keys())
        w.writeheader()
        w.writerows(all_exceptions)

# ─────────────────────────────────────────────────────────────────────────────
# Scorecard summary
# ─────────────────────────────────────────────────────────────────────────────

print("\n" + "=" * 65)
print("MASTER DATA QUALITY SCORECARD")
print("=" * 65)
print(f"{'Domain':<15} {'CRITICAL':>10} {'HIGH':>8} {'MEDIUM':>8} {'TOTAL':>8}")
print("-" * 55)

total_violations = 0
for domain in ["Material", "BOM", "Supplier", "Inventory"]:
    s = summary[domain]
    total_violations += s["total"]
    print(f"{domain:<15} {s['CRITICAL']:>10} {s['HIGH']:>8} {s['MEDIUM']:>8} {s['total']:>8}")

print("-" * 55)
print(f"{'TOTAL':<15} {sum(s['CRITICAL'] for s in summary.values()):>10} "
      f"{sum(s['HIGH'] for s in summary.values()):>8} "
      f"{sum(s['MEDIUM'] for s in summary.values()):>8} "
      f"{total_violations:>8}")

print(f"\nException file: {exc_path}")

# ─────────────────────────────────────────────────────────────────────────────
# BONUS: Window function - supplier on-time delivery rate
# ─────────────────────────────────────────────────────────────────────────────

OTD_SQL = """
SELECT
    poh.supplier_id,
    sm.supplier_name,
    sm.risk_category,
    COUNT(*)                                                      AS total_po_lines,
    SUM(CASE WHEN pol.actual_delivery_date IS NOT NULL
             AND DATE(pol.actual_delivery_date) <= DATE(pol.promised_delivery_date)
             THEN 1 ELSE 0 END)                                   AS on_time_lines,
    SUM(CASE WHEN pol.actual_delivery_date IS NULL
             THEN 1 ELSE 0 END)                                   AS not_delivered,
    ROUND(
        100.0 * SUM(CASE WHEN pol.actual_delivery_date IS NOT NULL
                         AND DATE(pol.actual_delivery_date) <= DATE(pol.promised_delivery_date)
                         THEN 1 ELSE 0 END)
        / NULLIF(COUNT(*) - SUM(CASE WHEN pol.actual_delivery_date IS NULL
                                     THEN 1 ELSE 0 END), 0),
    1) AS otd_pct,
    RANK() OVER (ORDER BY
        ROUND(
            100.0 * SUM(CASE WHEN pol.actual_delivery_date IS NOT NULL
                             AND DATE(pol.actual_delivery_date) <= DATE(pol.promised_delivery_date)
                             THEN 1 ELSE 0 END)
            / NULLIF(COUNT(*) - SUM(CASE WHEN pol.actual_delivery_date IS NULL
                                         THEN 1 ELSE 0 END), 0),
        1) ASC
    ) AS otd_rank_worst_first
FROM purchase_order_header poh
JOIN purchase_order_line   pol ON pol.po_number = poh.po_number
JOIN supplier_master       sm  ON sm.supplier_id = poh.supplier_id
WHERE poh.status != 'CANCELLED'
GROUP BY poh.supplier_id, sm.supplier_name, sm.risk_category
HAVING COUNT(*) >= 3
ORDER BY otd_pct ASC
"""

print("\n=== BONUS: Supplier On-Time Delivery (worst performers) ===")
otd_rows = conn.execute(OTD_SQL).fetchall()
print(f"{'Supplier':<12} {'Name':<28} {'OTD%':>6} {'Lines':>6} {'Not Del':>8} {'Risk':<8}")
print("-" * 80)
for r in otd_rows[:10]:
    otd = r["otd_pct"] if r["otd_pct"] else "N/A"
    print(f"{r['supplier_id']:<12} {(r['supplier_name'] or '')[:27]:<28} "
          f"{str(otd):>6} {r['total_po_lines']:>6} {r['not_delivered']:>8} {r['risk_category']:<8}")

print("""
KEY LEARNINGS:
1. Data quality rules follow a pattern: SELECT entity_id + tags WHERE <condition fails>.
   Every rule is a SELECT statement. The "good data" condition is just flipped to
   catch violations. You can add hundreds of rules without changing the framework.

2. Exception tables have a specific grain: one row per entity per rule violation.
   This lets you filter by rule, domain, severity, or plant independently.
   Don't collapse exceptions too early - the detail column needs to be diagnostic.

3. Severity matters operationally:
   - CRITICAL = system won't function (materials without plant views, invalid UOM)
   - HIGH = wrong outputs (MRP runs with bad data, AP can't pay)
   - MEDIUM = risk accumulation (coverage gaps, missing enrichment)

4. Window functions (RANK, ROW_NUMBER, LAG) are essential for:
   - Ranking suppliers/materials by performance
   - Detecting duplicates (partition by key, row_number > 1 = duplicate)
   - Comparing current vs previous period values
   These are the most-tested SQL patterns in data engineering interviews.
""")
