# Manufacturing Master Data Portfolio

**Role target:** Product Master Data Engineer | Material Master Data Analyst | Supply Chain Data Engineer

---

## Business Problem

Manufacturing companies run on master data. When material records are incomplete, BOMs reference obsolete components, or duplicate suppliers flood the system, the results are real: wrong shipments, production stoppages, MRP miscalculations, and audit failures. This portfolio demonstrates the ability to build the pipelines, quality frameworks, and analytics models that prevent those failures.

---

## Project Map

| Project | File | Core Skills | Business Value |
|---------|------|-------------|----------------|
| 1. BOM Explosion + Cost Rollup | `sql/project1_bom_explosion.py` | Recursive CTEs, effective-dated joins, cost rollup | Costing, impact analysis, obsolescence risk |
| 2. Master Data Quality Scorecard | `sql/project2_dq_scorecard.py` | DQ rule framework, exception tables, window functions, supplier OTD | Governance, MRP accuracy, procurement reliability |
| 3. Material Pipeline + Inventory Aging | `pyspark/project3_material_pipeline.py` | Raw-to-curated pipeline, deduplication, Parquet, audit logs | Standardization, excess/obsolete inventory analytics |

---

## How to Run

```bash
# 1. Generate all synthetic data (run this first)
python data/generate_data.py

# 2. Project 1 - BOM Explosion and Cost Rollup
python sql/project1_bom_explosion.py

# 3. Project 2 - Data Quality Scorecard
python sql/project2_dq_scorecard.py

# 4. Project 3 - Material Master Pipeline
python pyspark/project3_material_pipeline.py
```

No external dependencies beyond Python standard library + pandas + pyarrow.
Install: `pip install pandas pyarrow`

---

## Data Model

Simulated SAP-like tables (all synthetic):

```
material_master          (200 rows) - Core material attributes, lifecycle, cost, UOM
plant_material_view      (355 rows) - Plant-specific material extensions
bom_header               (70 rows)  - BOM identifiers, validity dates, status
bom_item                 (441 rows) - Parent-child component relationships
supplier_master          (100 rows) - Supplier records with intentional duplicates
purchase_order_header    (200 rows) - PO-level data
purchase_order_line      (562 rows) - PO line items with delivery dates
inventory_balance        (160 rows) - Stock quantities, values, last movement
```

Intentional data quality issues seeded in raw data:
- ~5% materials missing description
- ~4% materials with invalid UOM
- ~8% active materials missing lead time
- ~7% active materials missing MRP controller
- ~6% materials with reorder point but no lead time
- ~25% late deliveries across PO lines
- ~5% undelivered PO lines
- Duplicate supplier name variants

---

## Key Concepts Demonstrated

**Recursive CTEs (Project 1)**
The only SQL construct that handles unlimited-depth hierarchies. The BOM explosion anchor starts from finished goods; the recursive member walks down through sub-assemblies and raw materials. Without this, you need application-side recursion or multiple fixed-depth queries.

**Effective-dated joins (Project 1)**
`DATE('now') BETWEEN valid_from AND valid_to` ensures queries only use records valid today. Manufacturing BOMs, prices, supplier approvals, and routings all have validity periods. Missing this filter pulls expired data into planning and costing outputs.

**DQ rule framework (Project 2)**
Every rule is a SELECT that returns violating rows with a standardized schema: entity_id, rule_id, severity, detail. This makes the exception table filterable by any dimension. Adding a new rule means adding one SQL block - the framework does not need to change.

**Deduplication with rank-in-partition (Project 3)**
Group by the match key (normalized name + country), rank by created date, flag rank > 1 as duplicate candidates. The oldest record becomes the golden record. This same pattern runs in PySpark with `Window.partitionBy().orderBy()`.

**Raw-to-curated pipeline pattern (Project 3)**
Each zone has a contract:
- Raw: exact source copy, no changes
- Standardized: column names, types, codes normalized
- Curated: entities enriched and joined
- Analytics: marts built for specific use cases

Audit logs at each stage record row counts, timestamps, and exception counts. Without this, debugging production issues requires guesswork.

---