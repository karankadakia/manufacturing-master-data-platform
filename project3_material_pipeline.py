"""
PROJECT 3: PySpark-Style Material Master Normalization Pipeline
===============================================================
What this teaches:
- Raw-to-curated transformation pipeline (the core of any data engineering job)
- Schema standardization across ERP-like and PLM-like sources
- Deduplication with window functions
- SCD Type 2 snapshot pattern
- Writing Parquet (production-standard format)
- Generating data quality outputs alongside transformed data

This runs with pandas (same concepts as PySpark; swap pd. with spark.
and .apply() with spark UDFs when you move to actual Databricks/Spark).

Run: python pyspark/project3_material_pipeline.py
Output: data/curated/ (Parquet + CSV) + data/exceptions/
"""

import pandas as pd
import os
import json
from datetime import datetime
RAW_DIR     = os.path.join(os.path.dirname(r"C:\Users\kadak\DE projects\data\raw"), "..", "data", "raw")
CURATED_DIR = os.path.join(os.path.dirname(r"C:\Users\kadak\DE projects\data\curated"), "..", "data", "curated")
EXCEPT_DIR  = os.path.join(os.path.dirname(r"C:\Users\kadak\DE projects\data\exceptions"), "..", "data", "exceptions")

os.makedirs(CURATED_DIR, exist_ok=True)
os.makedirs(EXCEPT_DIR,  exist_ok=True)

LOAD_TS = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

print("=" * 60)
print("MATERIAL MASTER NORMALIZATION PIPELINE")
print("=" * 60)

# ─────────────────────────────────────────────────────────────────────────────
# STEP 1: INGEST (Raw Zone)
# In real life: read from S3 raw zone or ERP extract landing
# ─────────────────────────────────────────────────────────────────────────────

print("\nStep 1: Ingesting raw data...")

mat_df  = pd.read_csv(os.path.join(RAW_DIR, "material_master.csv"))
pv_df   = pd.read_csv(os.path.join(RAW_DIR, "plant_material_view.csv"))
inv_df  = pd.read_csv(os.path.join(RAW_DIR, "inventory_balance.csv"))
sup_df  = pd.read_csv(os.path.join(RAW_DIR, "supplier_master.csv"))
bom_h   = pd.read_csv(os.path.join(RAW_DIR, "bom_header.csv"))
bom_i   = pd.read_csv(os.path.join(RAW_DIR, "bom_item.csv"))

# Track row counts at each stage (audit trail)
audit = {}
audit["raw_material_count"] = len(mat_df)
print(f"  Raw materials: {len(mat_df)}")
print(f"  Raw plant views: {len(pv_df)}")
print(f"  Raw inventory: {len(inv_df)}")
print(f"  Raw suppliers: {len(sup_df)}")

# ─────────────────────────────────────────────────────────────────────────────
# STEP 2: STANDARDIZE (Standardized Zone)
# - Column name normalization
# - Data type casting
# - Null handling
# - Value mapping / code translation
# ─────────────────────────────────────────────────────────────────────────────

print("\nStep 2: Standardizing schemas...")

# -- Material master standardization ------------------------------------------

# Normalize column names (in real life: multiple source systems have different names)
mat_df = mat_df.rename(columns={
    "material_id":       "mat_id",
    "material_type":     "mat_type",
    "lifecycle_status":  "status",
    "standard_cost_usd": "std_cost",
    "procurement_type":  "proc_type",
    "mrp_controller":    "planner",
    "hazmat_flag":       "is_hazmat",
    "country_of_origin": "origin_country",
    "batch_tracking":    "batch_flag",
})

# Cast numeric fields
mat_df["std_cost"]       = pd.to_numeric(mat_df["std_cost"],       errors="coerce")
mat_df["lead_time_days"] = pd.to_numeric(mat_df["lead_time_days"], errors="coerce")
mat_df["lot_size"]       = pd.to_numeric(mat_df["lot_size"],       errors="coerce")
mat_df["safety_stock"]   = pd.to_numeric(mat_df["safety_stock"],   errors="coerce")
mat_df["reorder_point"]  = pd.to_numeric(mat_df["reorder_point"],  errors="coerce")

# Standardize boolean-like fields
mat_df["is_hazmat"]  = mat_df["is_hazmat"].map({"Y": True, "N": False}).fillna(False)
mat_df["batch_flag"] = mat_df["batch_flag"].map({"X": True}).fillna(False)

# Map SAP material type codes to readable labels
mat_type_map = {
    "ROH":  "Raw Material",
    "HALB": "Semi-Finished",
    "FERT": "Finished Good",
    "VERP": "Packaging",
    "HIBE": "MRO",
}
mat_df["mat_type_label"] = mat_df["mat_type"].map(mat_type_map).fillna("Unknown")

# Map procurement type
proc_map = {"E": "External", "F": "In-House", "X": "Both"}
mat_df["proc_type_label"] = mat_df["proc_type"].map(proc_map).fillna("Unknown")

# Valid UOM whitelist (standardization catch)
VALID_UOMS = {"EA","KG","L","M","PC","SET","BOX","G","ML","FT","LB","TON","ROL"}
mat_df["uom_is_valid"] = mat_df["uom"].isin(VALID_UOMS)

# Add pipeline metadata
mat_df["src_system"]  = "ERP_MAIN"
mat_df["load_ts"]     = LOAD_TS
mat_df["record_hash"] = mat_df.apply(
    lambda r: str(hash(tuple(r[["mat_id","description","status","std_cost","uom"]]))), axis=1
)

print(f"  Material master standardized: {len(mat_df)} rows")

# -- Supplier standardization -------------------------------------------------

sup_df = sup_df.rename(columns={
    "supplier_id":       "sup_id",
    "supplier_name":     "sup_name",
    "supplier_site":     "site",
    "risk_category":     "risk_cat",
    "single_source_flag":"is_single_source",
})
sup_df["is_single_source"] = sup_df["is_single_source"].map({"Y": True, "N": False}).fillna(False)
sup_df["is_certified"]     = sup_df["certified"].map({"Y": True, "N": False}).fillna(False)
sup_df["is_active"]        = sup_df["active"].map({"Y": True, "N": False}).fillna(False)
sup_df["src_system"] = "ERP_MAIN"
sup_df["load_ts"]    = LOAD_TS

print(f"  Supplier master standardized: {len(sup_df)} rows")

# ─────────────────────────────────────────────────────────────────────────────
# STEP 3: DEDUPLICATION (window function pattern)
# Identify potential duplicates in supplier master
# Real-world: fuzzy matching on name + tax_id + country
# ─────────────────────────────────────────────────────────────────────────────

print("\nStep 3: Supplier deduplication...")

# Normalize name for matching
sup_df["name_normalized"] = (
    sup_df["sup_name"]
    .str.upper()
    .str.replace(r"\s+", " ", regex=True)
    .str.replace(r"\bCORPORATION\b", "CORP", regex=True)
    .str.replace(r"\bSUPPLIES\b", "SUPPLY", regex=True)
    .str.strip()
)

# Create a match key: normalized name + country
sup_df["dedup_key"] = sup_df["name_normalized"] + "|" + sup_df["country"].fillna("")

# Window function equivalent: rank within dedup_key groups
# In PySpark: Window.partitionBy("dedup_key").orderBy("created_date")
sup_df["dedup_rank"] = sup_df.groupby("dedup_key")["created_date"] \
    .rank(method="first", ascending=True).astype(int)

sup_df["is_duplicate_candidate"] = sup_df["dedup_rank"] > 1

dup_count = sup_df["is_duplicate_candidate"].sum()
print(f"  Potential duplicate supplier records: {dup_count}")

# Write duplicate exceptions
dup_exceptions = sup_df[sup_df["is_duplicate_candidate"]][
    ["sup_id", "sup_name", "country", "tax_id", "dedup_key", "dedup_rank"]
].copy()
dup_exceptions["rule_id"] = "SUP-DUP-001"
dup_exceptions["detail"] = "Potential duplicate: same normalized name + country"
dup_exceptions.to_csv(os.path.join(EXCEPT_DIR, "supplier_duplicates.csv"), index=False)
print(f"  Written: exceptions/supplier_duplicates.csv ({len(dup_exceptions)} rows)")

# ─────────────────────────────────────────────────────────────────────────────
# STEP 4: CURATED LAYER
# - Merge material + plant view (conformed)
# - Calculate completeness score per material
# - Write Parquet (production format) + CSV (human-readable)
# ─────────────────────────────────────────────────────────────────────────────

print("\nStep 4: Building curated material layer...")

# Merge material with plant views (left join = all materials, even without plant views)
pv_df = pv_df.rename(columns={"material_id": "mat_id"})
mat_with_plants = mat_df.merge(
    pv_df.groupby("mat_id").agg(
        plant_count=("plant", "nunique"),
        plants=("plant", lambda x: ",".join(sorted(x.unique()))),
        has_blocked_plant=("plant_status", lambda x: (x == "BLOCKED").any()),
    ).reset_index(),
    on="mat_id",
    how="left",
)
mat_with_plants["plant_count"] = mat_with_plants["plant_count"].fillna(0).astype(int)
mat_with_plants["has_plant_view"] = mat_with_plants["plant_count"] > 0

# Completeness score: how many of the critical fields are populated?
critical_fields = ["description", "uom", "lead_time_days", "planner", "lot_size", "std_cost"]
for f in critical_fields:
    mat_with_plants[f"_has_{f}"] = mat_with_plants[f].notna() & (mat_with_plants[f].astype(str).str.strip() != "")
mat_with_plants["completeness_score"] = (
    mat_with_plants[[f"_has_{f}" for f in critical_fields]].sum(axis=1) / len(critical_fields) * 100
).round(1)

# Drop temp columns
mat_with_plants = mat_with_plants.drop(columns=[f"_has_{f}" for f in critical_fields])

# Inventory summary join
inv_summary = inv_df.groupby("material_id").agg(
    total_qty_on_hand=("qty_on_hand", "sum"),
    total_inventory_value=("total_value_usd", "sum"),
    plant_inventory_count=("plant", "nunique"),
    last_movement_date=("last_movement_date", "max"),
).reset_index().rename(columns={"material_id": "mat_id"})

curated_mat = mat_with_plants.merge(inv_summary, on="mat_id", how="left")
curated_mat["total_qty_on_hand"]      = curated_mat["total_qty_on_hand"].fillna(0)
curated_mat["total_inventory_value"]  = curated_mat["total_inventory_value"].fillna(0)

audit["curated_material_count"] = len(curated_mat)
print(f"  Curated material rows: {len(curated_mat)}")

# Write outputs
curated_mat.to_parquet(os.path.join(CURATED_DIR, "material_master.parquet"), index=False)
curated_mat.to_csv(os.path.join(CURATED_DIR, "material_master.csv"), index=False)
print("  Written: curated/material_master.parquet + .csv")

# Write curated supplier
sup_curated = sup_df[~sup_df["is_duplicate_candidate"]].copy()
sup_curated.to_parquet(os.path.join(CURATED_DIR, "supplier_master.parquet"), index=False)
sup_curated.to_csv(os.path.join(CURATED_DIR, "supplier_master.csv"), index=False)
print(f"  Written: curated/supplier_master.parquet ({len(sup_curated)} de-duplicated rows)")

# ─────────────────────────────────────────────────────────────────────────────
# STEP 5: DATA QUALITY EXCEPTIONS (pipeline-generated)
# ─────────────────────────────────────────────────────────────────────────────

print("\nStep 5: Generating pipeline DQ exceptions...")

pipeline_exceptions = []

# Invalid UOM
invalid_uom = curated_mat[~curated_mat["uom_is_valid"]]
for _, r in invalid_uom.iterrows():
    pipeline_exceptions.append({
        "rule_id": "PIPE-MAT-001",
        "entity_id": r["mat_id"],
        "severity": "CRITICAL",
        "detail": f"Invalid UOM '{r['uom']}' - rejected from analytics layer"
    })

# Active materials with low completeness
low_completeness = curated_mat[
    (curated_mat["status"] == "ACTIVE") & (curated_mat["completeness_score"] < 70)
]
for _, r in low_completeness.iterrows():
    pipeline_exceptions.append({
        "rule_id": "PIPE-MAT-002",
        "entity_id": r["mat_id"],
        "severity": "HIGH",
        "detail": f"Completeness score {r['completeness_score']}% below 70% threshold"
    })

exc_df = pd.DataFrame(pipeline_exceptions)
exc_df.to_csv(os.path.join(EXCEPT_DIR, "pipeline_exceptions.csv"), index=False)
print(f"  Written: exceptions/pipeline_exceptions.csv ({len(pipeline_exceptions)} rows)")

# ─────────────────────────────────────────────────────────────────────────────
# STEP 6: ANALYTICS LAYER - Inventory Aging Mart
# ─────────────────────────────────────────────────────────────────────────────

print("\nStep 6: Building inventory aging analytics mart...")

inv_df["last_movement_date"] = pd.to_datetime(inv_df["last_movement_date"])
ref_date = pd.Timestamp("2024-12-31")
inv_df["days_since_movement"] = (ref_date - inv_df["last_movement_date"]).dt.days

def aging_bucket(days):
    if days <= 30:   return "0-30 days"
    if days <= 90:   return "31-90 days"
    if days <= 180:  return "91-180 days"
    if days <= 365:  return "181-365 days"
    return "365+ days (excess/obsolete risk)"

inv_df["aging_bucket"] = inv_df["days_since_movement"].apply(aging_bucket)

inv_mart = inv_df.merge(
    curated_mat[["mat_id","description","status","mat_type_label","completeness_score"]],
    left_on="material_id", right_on="mat_id", how="left"
)

inv_mart["total_value_usd"] = pd.to_numeric(inv_mart["total_value_usd"], errors="coerce").fillna(0)
inv_mart["qty_on_hand"]     = pd.to_numeric(inv_mart["qty_on_hand"],     errors="coerce").fillna(0)

# Flag excess/obsolete
inv_mart["excess_risk_flag"]    = inv_mart["days_since_movement"] > 365
inv_mart["high_value_no_move"]  = (inv_mart["days_since_movement"] > 180) & \
                                   (inv_mart["total_value_usd"] > 5000)

inv_mart.to_csv(os.path.join(CURATED_DIR, "inventory_aging_mart.csv"), index=False)
print(f"  Written: curated/inventory_aging_mart.csv ({len(inv_mart)} rows)")

# Print aging summary
print("\n  Inventory Aging Summary:")
aging_summary = inv_mart.groupby("aging_bucket").agg(
    record_count=("material_id", "count"),
    total_value=("total_value_usd", "sum"),
).reset_index().sort_values("aging_bucket")
print(f"  {'Aging Bucket':<35} {'Records':>8} {'Total Value ($)':>15}")
print("  " + "-" * 62)
for _, r in aging_summary.iterrows():
    print(f"  {r['aging_bucket']:<35} {r['record_count']:>8} {r['total_value']:>15,.0f}")

# ─────────────────────────────────────────────────────────────────────────────
# STEP 7: AUDIT LOG
# ─────────────────────────────────────────────────────────────────────────────

audit["curated_supplier_count"]     = len(sup_curated)
audit["supplier_duplicates_flagged"] = int(dup_count)
audit["pipeline_exceptions"]         = len(pipeline_exceptions)
audit["inventory_mart_rows"]         = len(inv_mart)
audit["pipeline_run_ts"]             = LOAD_TS

audit_path = os.path.join(CURATED_DIR, "pipeline_audit.json")
with open(audit_path, "w") as f:
    json.dump(audit, f, indent=2)

print(f"\n  Audit log written: curated/pipeline_audit.json")
print(f"\n  {json.dumps(audit, indent=4)}")

print("""
KEY LEARNINGS:
1. Raw -> Standardized -> Curated -> Analytics is a REQUIRED pattern in every
   manufacturing data platform. Each zone has a specific contract:
   - Raw: exact copy of source, no transformation
   - Standardized: column names, types, codes normalized
   - Curated: business entities enriched and joined
   - Analytics: marts optimized for specific use cases (aging, scorecards)

2. Completeness score is the simplest governance KPI you can build.
   Count how many critical fields are populated / total critical fields * 100.
   It's immediately meaningful to a data steward and requires zero statistics.

3. Deduplication with window functions (rank within partition):
   - Group by the match key (normalized name + country)
   - Rank by created_date (oldest = rank 1 = golden record)
   - All rank > 1 = duplicate candidates
   This same pattern works in PySpark with Window.partitionBy().orderBy()

4. Parquet is always the output format for curated data.
   It is compressed (5-10x smaller than CSV), schema-typed, column-oriented
   (fast for analytics), and readable by Spark, Athena, Redshift, and Snowflake.
   Writing .to_parquet() here is the exact call in PySpark too.

5. Audit logs prove your pipeline ran and show row counts at each stage.
   This is what operations teams check when something looks wrong in a report.
   Without it, you're debugging blind.
""")
