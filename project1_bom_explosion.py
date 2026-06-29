"""
PROJECT 1: Multi-Level BOM Explosion + Cost Rollup
===================================================
What this teaches:
- Recursive CTEs (the most important SQL skill for manufacturing data)
- Effective-dated joins (only pick BOMs valid TODAY)
- Cost rollup across BOM levels
- Impact analysis: which finished goods use an obsolete component?

Run: python sql/project1_bom_explosion.py
Output: prints results + writes to data/curated/bom_explosion.csv
"""

import sqlite3
import csv
import os

RAW_DIR     = os.path.join(os.path.dirname(r"C:\Users\kadak\DE projects\data\raw"), "..", "data", "raw")
CURATED_DIR = os.path.join(os.path.dirname(r"C:\Users\kadak\DE projects\data\curated"), "..", "data", "curated")
os.makedirs(CURATED_DIR, exist_ok=True)

# ── Load CSVs into in-memory SQLite ──────────────────────────────────────────

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
    print(f"  Loaded {table}: {len(rows)} rows")

conn = sqlite3.connect(":memory:")
conn.row_factory = sqlite3.Row

print("Loading data...")
load_csv(conn, "material_master",   os.path.join(RAW_DIR, "material_master.csv"))
load_csv(conn, "bom_header",        os.path.join(RAW_DIR, "bom_header.csv"))
load_csv(conn, "bom_item",          os.path.join(RAW_DIR, "bom_item.csv"))

# ─────────────────────────────────────────────────────────────────────────────
# QUERY 1: Multi-Level BOM Explosion
# Key learning: recursive CTE
# The WITH RECURSIVE walks parent → child → grandchild ... to any depth.
# ─────────────────────────────────────────────────────────────────────────────

BOM_EXPLOSION_SQL = """
WITH RECURSIVE bom_explosion AS (

    -- ANCHOR: start from finished goods (level 0)
    SELECT
        bh.parent_material          AS root_material,
        bh.parent_material          AS parent_material,
        bi.component_material       AS component_material,
        bi.quantity                 AS qty_per_parent,
        CAST(bi.quantity AS REAL)   AS extended_qty,   -- qty relative to root
        1                           AS bom_level,
        bh.bom_number,
        bi.is_phantom,
        bi.valid_from,
        bi.valid_to
    FROM bom_header bh
    JOIN bom_item   bi ON bi.bom_number = bh.bom_number
    WHERE bh.bom_status = 'ACTIVE'
      AND DATE('now') BETWEEN DATE(bh.valid_from) AND DATE(bh.valid_to)

    UNION ALL

    -- RECURSIVE: walk down into sub-assemblies
    SELECT
        be.root_material,
        be.component_material       AS parent_material,
        bi.component_material       AS component_material,
        bi.quantity                 AS qty_per_parent,
        ROUND(be.extended_qty * CAST(bi.quantity AS REAL), 6) AS extended_qty,
        be.bom_level + 1,
        bh.bom_number,
        bi.is_phantom,
        bi.valid_from,
        bi.valid_to
    FROM bom_explosion be
    JOIN bom_header bh ON bh.parent_material = be.component_material
                       AND bh.bom_status = 'ACTIVE'
                       AND DATE('now') BETWEEN DATE(bh.valid_from) AND DATE(bh.valid_to)
    JOIN bom_item   bi ON bi.bom_number = bh.bom_number
    WHERE be.bom_level < 5  -- safety: prevent infinite loops
)

SELECT
    be.root_material,
    be.bom_level,
    be.parent_material,
    be.component_material,
    mm.description                     AS component_description,
    mm.material_type,
    mm.lifecycle_status,
    ROUND(be.extended_qty, 4)          AS extended_qty,
    mm.uom,
    ROUND(be.extended_qty * CAST(COALESCE(mm.standard_cost_usd, 0) AS REAL), 2)
                                       AS extended_cost_usd,
    be.is_phantom
FROM bom_explosion be
LEFT JOIN material_master mm ON mm.material_id = be.component_material
ORDER BY be.root_material, be.bom_level, be.parent_material, be.component_material
"""

print("\n=== QUERY 1: Multi-Level BOM Explosion ===")
rows = conn.execute(BOM_EXPLOSION_SQL).fetchall()
print(f"Total BOM explosion rows: {len(rows)}")
print("\nSample (first 10 rows):")
headers = ["root_material","bom_level","parent_material","component_material",
           "lifecycle_status","extended_qty","uom","extended_cost_usd","is_phantom"]
print("  " + "  |  ".join(f"{h:<20}" for h in headers))
print("  " + "-" * 160)
for r in rows[:10]:
    print("  " + "  |  ".join(f"{str(r[h]):<20}" for h in headers))

# Write to curated
with open(os.path.join(CURATED_DIR, "bom_explosion.csv"), "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(rows[0].keys() if rows else [])
    w.writerows([tuple(r) for r in rows])

# ─────────────────────────────────────────────────────────────────────────────
# QUERY 2: Cost Rollup - total material cost per finished good
# Business use: costing, pricing, margin analysis
# ─────────────────────────────────────────────────────────────────────────────

COST_ROLLUP_SQL = f"""
WITH bom_exploded AS ({BOM_EXPLOSION_SQL.strip()})
SELECT
    root_material,
    COUNT(DISTINCT component_material)  AS total_components,
    COUNT(DISTINCT bom_level)           AS max_depth,
    ROUND(SUM(extended_cost_usd), 2)    AS total_bom_cost_usd,
    SUM(CASE WHEN lifecycle_status = 'OBSOLETE'  THEN 1 ELSE 0 END) AS obsolete_components,
    SUM(CASE WHEN lifecycle_status = 'BLOCKED'   THEN 1 ELSE 0 END) AS blocked_components,
    SUM(CASE WHEN is_phantom = 'Y'               THEN 1 ELSE 0 END) AS phantom_components
FROM bom_exploded
GROUP BY root_material
ORDER BY total_bom_cost_usd DESC
"""

print("\n=== QUERY 2: Cost Rollup per Finished Good ===")
rollup_rows = conn.execute(COST_ROLLUP_SQL).fetchall()
print(f"Finished goods with BOM: {len(rollup_rows)}")
print("\nTop 10 by BOM cost:")
print(f"  {'root_material':<15}  {'components':<12}  {'depth':<7}  {'total_cost_usd':<16}  {'obsolete':<10}  {'blocked':<8}")
print("  " + "-" * 80)
for r in rollup_rows[:10]:
    print(f"  {r['root_material']:<15}  {r['total_components']:<12}  "
          f"{r['max_depth']:<7}  {r['total_bom_cost_usd']:<16}  "
          f"{r['obsolete_components']:<10}  {r['blocked_components']:<8}")

# ─────────────────────────────────────────────────────────────────────────────
# QUERY 3: Impact Analysis - which finished goods use an obsolete component?
# Business use: obsolescence risk - operations needs to know BEFORE production
# ─────────────────────────────────────────────────────────────────────────────

IMPACT_SQL = f"""
WITH bom_exploded AS ({BOM_EXPLOSION_SQL.strip()})
SELECT
    component_material,
    component_description,
    lifecycle_status,
    COUNT(DISTINCT root_material) AS affected_finished_goods,
    GROUP_CONCAT(DISTINCT root_material) AS finished_goods_list
FROM bom_exploded
WHERE lifecycle_status IN ('OBSOLETE', 'BLOCKED', 'DISCONTINUED')
GROUP BY component_material, component_description, lifecycle_status
HAVING COUNT(DISTINCT root_material) > 0
ORDER BY affected_finished_goods DESC
"""

print("\n=== QUERY 3: Impact Analysis - Obsolete Components in Active BOMs ===")
impact_rows = conn.execute(IMPACT_SQL).fetchall()
print(f"Problematic components: {len(impact_rows)}")
if impact_rows:
    print("\nTop 5 highest-impact obsolete components:")
    for r in impact_rows[:5]:
        print(f"  {r['component_material']}  status={r['lifecycle_status']}"
              f"  affects {r['affected_finished_goods']} finished goods")
        if r['finished_goods_list']:
            print(f"    Finished goods: {r['finished_goods_list'][:120]}")

print("\nProject 1 complete. Output: data/curated/bom_explosion.csv")
print("""
KEY LEARNINGS:
1. Recursive CTE = the only way to handle multi-level hierarchies in SQL.
   The ANCHOR defines the starting rows; RECURSIVE adds the next level
   each pass until no new rows are found (or bom_level >= limit).

2. Effective-dated joins: DATE('now') BETWEEN valid_from AND valid_to
   ensures you only use BOMs valid TODAY, not expired or future ones.
   Without this, you'd unknowingly pull outdated product structures.

3. Cost rollup: extended_qty * unit_cost rolls up FROM leaf to root.
   At each level you multiply the parent quantity by the child quantity.
   This is why 'extended_qty' carries the cumulative factor, not just
   the direct component quantity.

4. Impact analysis: always flip the direction. Instead of "what's IN
   the BOM of X", ask "which BOMs CONTAIN this problem component?"
   This is what operations actually needs during an EOL/shortage event.
""")
