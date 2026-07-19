#!/usr/bin/env python3
"""
Create all 8 Iceberg table definitions in Glue catalog (no CloudFormation needed).
Run once after the S3 bucket and Glue DB exist.
"""
import boto3, json, sys

REGION   = "ap-southeast-2"
ACCOUNT  = "020396275984"
BUCKET   = f"vbc-poc-{ACCOUNT}"
DATABASE = "vbc_poc_db"

glue = boto3.client("glue", region_name=REGION)

# Column type helper
def col(name, typ, comment=""):
    return {"Name": name, "Type": typ, "Comment": comment}

TABLES = {
    "member_master": {
        "description": "Domain 1 — Patient/Member master",
        "columns": [
            col("member_id",           "string",  "Surrogate member key"),
            col("mrn",                 "string",  "Medical Record Number"),
            col("first_name",          "string"),
            col("last_name",           "string"),
            col("dob",                 "date"),
            col("gender",              "string"),
            col("race",                "string"),
            col("ethnicity",           "string"),
            col("preferred_language",  "string"),
            col("address_line1",       "string"),
            col("city",                "string"),
            col("state",               "string"),
            col("zip_code",            "string"),
            col("county",              "string"),
            col("phone",               "string"),
            col("email",               "string"),
            col("eligibility_start",   "date"),
            col("eligibility_end",     "date"),
            col("payer_id",            "string"),
            col("plan_id",             "string"),
            col("pcp_npi",             "string"),
            col("adi_score",           "int",     "Area Deprivation Index 0-100"),
            col("risk_tier",           "string",  "low/moderate/high"),
            col("attribution_status",  "string"),
            col("created_at",          "timestamp"),
            col("updated_at",          "timestamp"),
        ],
    },
    "provider_master": {
        "description": "Domain 2 — Provider master",
        "columns": [
            col("provider_id",         "string"),
            col("npi",                 "string",  "National Provider Identifier"),
            col("first_name",          "string"),
            col("last_name",           "string"),
            col("credential",          "string"),
            col("specialty",           "string"),
            col("provider_type",       "string",  "PCP/Specialist/Hospital"),
            col("network_id",          "string"),
            col("network_name",        "string"),
            col("tax_id",              "string"),
            col("group_npi",           "string"),
            col("practice_name",       "string"),
            col("address_line1",       "string"),
            col("city",                "string"),
            col("state",               "string"),
            col("zip_code",            "string"),
            col("phone",               "string"),
            col("accepting_patients",  "boolean"),
            col("panel_size",          "int"),
            col("created_at",          "timestamp"),
        ],
    },
    "claims_medical": {
        "description": "Domain 4 — Medical claims",
        "columns": [
            col("claim_id",            "string"),
            col("member_id",           "string"),
            col("provider_npi",        "string"),
            col("facility_npi",        "string"),
            col("service_date",        "date"),
            col("discharge_date",      "date"),
            col("claim_type",          "string",  "professional/institutional"),
            col("place_of_service",    "string"),
            col("drg_code",            "string",  "Diagnosis Related Group"),
            col("revenue_code",        "string"),
            col("cpt_code",            "string"),
            col("hcpcs_code",          "string"),
            col("units",               "int"),
            col("billed_amount",       "double"),
            col("allowed_amount",      "double"),
            col("paid_amount",         "double"),
            col("member_cost_share",   "double"),
            col("claim_status",        "string"),
            col("primary_icd10",       "string"),
            col("admit_type",          "string"),
            col("los_days",            "int",     "Length of stay"),
            col("readmission_flag",    "boolean"),
            col("created_at",          "timestamp"),
        ],
    },
    "diagnosis_history": {
        "description": "Domain 3 — Patient diagnosis history",
        "columns": [
            col("diagnosis_id",        "string"),
            col("member_id",           "string"),
            col("claim_id",            "string"),
            col("provider_npi",        "string"),
            col("icd10_cm_code",       "string"),
            col("icd10_description",   "string"),
            col("diagnosis_date",      "date"),
            col("diagnosis_type",      "string",  "primary/secondary/admitting"),
            col("hcc_code",            "string"),
            col("hcc_description",     "string"),
            col("raf_weight",          "double"),
            col("chronic_flag",        "boolean"),
            col("created_at",          "timestamp"),
        ],
    },
    "hedis_gaps": {
        "description": "Domain 6 — HEDIS quality gaps",
        "columns": [
            col("gap_id",              "string"),
            col("member_id",           "string"),
            col("pcp_npi",             "string"),
            col("measure_id",          "string",  "CDC, CBP, COL etc."),
            col("measure_name",        "string"),
            col("measure_year",        "int"),
            col("gap_status",          "string",  "open/closed/excluded"),
            col("open_date",           "date"),
            col("close_date",          "date"),
            col("exclusion_reason",    "string"),
            col("numerator_flag",      "boolean"),
            col("denominator_flag",    "boolean"),
            col("last_service_date",   "date"),
            col("created_at",          "timestamp"),
        ],
    },
    "risk_scores": {
        "description": "Domain 7 — Member risk scores",
        "columns": [
            col("score_id",            "string"),
            col("member_id",           "string"),
            col("score_type",          "string",  "HCC/CDPS/custom"),
            col("score_date",          "date"),
            col("score_period",        "string",  "e.g. 2025-H1"),
            col("raf_score",           "double"),
            col("risk_score_value",    "double"),
            col("risk_tier",           "string",  "low/moderate/high"),
            col("hcc_count",           "int"),
            col("top_hcc_code",        "string"),
            col("top_hcc_weight",      "double"),
            col("prospective_score",   "double"),
            col("concurrent_score",    "double"),
            col("created_at",          "timestamp"),
        ],
    },
    "sdoh_barriers": {
        "description": "Domain 8 — Social Determinants of Health barriers",
        "columns": [
            col("barrier_id",          "string"),
            col("member_id",           "string"),
            col("barrier_type",        "string",  "food/housing/transportation/social_isolation"),
            col("barrier_category",    "string"),
            col("icd10_z_code",        "string",  "Z55-Z65 social history codes"),
            col("identified_date",     "date"),
            col("resolved_date",       "date"),
            col("severity",            "string",  "mild/moderate/severe"),
            col("source",              "string",  "claim/screening/referral"),
            col("referral_made",       "boolean"),
            col("referral_org",        "string"),
            col("adi_score",           "int"),
            col("created_at",          "timestamp"),
        ],
    },
    "pharmacy_claims": {
        "description": "Domain 5 — Pharmacy claims",
        "columns": [
            col("rx_claim_id",         "string"),
            col("member_id",           "string"),
            col("prescriber_npi",      "string"),
            col("dispensing_npi",      "string"),
            col("fill_date",           "date"),
            col("ndc_code",            "string",  "National Drug Code"),
            col("drug_name",           "string"),
            col("drug_class",          "string"),
            col("rxnorm_code",         "string"),
            col("days_supply",         "int"),
            col("quantity",            "double"),
            col("refill_number",       "int"),
            col("billed_amount",       "double"),
            col("paid_amount",         "double"),
            col("generic_flag",        "boolean"),
            col("formulary_tier",      "int"),
            col("pdc_numerator",       "boolean", "Proportion of Days Covered"),
            col("high_alert_flag",     "boolean"),
            col("created_at",          "timestamp"),
        ],
    },
}


def make_table_input(name, spec):
    location = f"s3://{BUCKET}/iceberg/{name}/"
    return {
        "Name": name,
        "Description": spec["description"],
        "StorageDescriptor": {
            "Columns": spec["columns"],
            "Location": location,
            "InputFormat":  "org.apache.hadoop.mapred.FileInputFormat",
            "OutputFormat": "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat",
            "SerdeInfo": {
                "SerializationLibrary": "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe",
                "Parameters": {"serialization.format": "1"},
            },
            "Parameters": {
                "table_type":             "ICEBERG",
                "format":                 "parquet",
                "write_compression":      "snappy",
            },
        },
        "TableType": "EXTERNAL_TABLE",
        "Parameters": {
            "table_type":             "ICEBERG",
            "classification":         "parquet",
            "compressionType":        "snappy",
            "typeOfData":             "file",
        },
    }


def main():
    created, skipped, failed = [], [], []
    for name, spec in TABLES.items():
        try:
            glue.create_table(
                DatabaseName=DATABASE,
                TableInput=make_table_input(name, spec),
            )
            print(f"  ✅ Created: {name}")
            created.append(name)
        except glue.exceptions.AlreadyExistsException:
            print(f"  ⏭  Exists:  {name} (skipped)")
            skipped.append(name)
        except Exception as e:
            print(f"  ❌ Failed:  {name} — {e}")
            failed.append(name)

    print(f"\nSummary: {len(created)} created, {len(skipped)} skipped, {len(failed)} failed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
