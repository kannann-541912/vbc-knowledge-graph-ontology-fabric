#!/usr/bin/env python3
"""
Register Parquet files with Athena as external tables and validate row counts.
Runs CREATE EXTERNAL TABLE DDL for each dataset, then runs COUNT(*) to verify.
No Glue crawlers or CloudFormation needed.
"""
import boto3, time, sys

REGION   = "ap-southeast-2"
ACCOUNT  = "020396275984"
BUCKET   = f"vbc-poc-{ACCOUNT}"
DATABASE = "vbc_poc_db"
WORKGROUP = "primary"
OUTPUT   = f"s3://{BUCKET}/raw/athena-results/"

athena = boto3.client("athena", region_name=REGION)

TABLES = {
    "member_master": {
        "expected": 500,
        "ddl": f"""CREATE EXTERNAL TABLE IF NOT EXISTS {DATABASE}.member_master (
  member_id STRING, mrn STRING, first_name STRING, last_name STRING,
  dob DATE, gender STRING, race STRING, ethnicity STRING,
  preferred_language STRING, address_line1 STRING, city STRING,
  state STRING, zip_code STRING, county STRING, phone STRING, email STRING,
  eligibility_start DATE, eligibility_end DATE, payer_id STRING, plan_id STRING,
  pcp_npi STRING, adi_score INT, risk_tier STRING, attribution_status STRING,
  created_at TIMESTAMP, updated_at TIMESTAMP
)
STORED AS PARQUET
LOCATION 's3://{BUCKET}/iceberg/members/'
TBLPROPERTIES ('parquet.compress'='SNAPPY')""",
    },
    "provider_master": {
        "expected": 50,
        "ddl": f"""CREATE EXTERNAL TABLE IF NOT EXISTS {DATABASE}.provider_master (
  provider_id STRING, npi STRING, first_name STRING, last_name STRING,
  credential STRING, specialty STRING, provider_type STRING,
  network_id STRING, network_name STRING, tax_id STRING, group_npi STRING,
  practice_name STRING, address_line1 STRING, city STRING, state STRING,
  zip_code STRING, phone STRING, accepting_patients BOOLEAN, panel_size INT,
  created_at TIMESTAMP
)
STORED AS PARQUET
LOCATION 's3://{BUCKET}/iceberg/providers/'
TBLPROPERTIES ('parquet.compress'='SNAPPY')""",
    },
    "claims_medical": {
        "expected": 2000,
        "ddl": f"""CREATE EXTERNAL TABLE IF NOT EXISTS {DATABASE}.claims_medical (
  claim_id STRING, member_id STRING, provider_npi STRING, facility_npi STRING,
  service_date DATE, discharge_date DATE, claim_type STRING, place_of_service STRING,
  drg_code STRING, revenue_code STRING, cpt_code STRING, hcpcs_code STRING,
  units INT, billed_amount DOUBLE, allowed_amount DOUBLE, paid_amount DOUBLE,
  member_cost_share DOUBLE, claim_status STRING, primary_icd10 STRING,
  admit_type STRING, los_days INT, readmission_flag BOOLEAN, created_at TIMESTAMP
)
STORED AS PARQUET
LOCATION 's3://{BUCKET}/iceberg/claims/'
TBLPROPERTIES ('parquet.compress'='SNAPPY')""",
    },
    "diagnosis_history": {
        "expected": 3000,
        "ddl": f"""CREATE EXTERNAL TABLE IF NOT EXISTS {DATABASE}.diagnosis_history (
  diagnosis_id STRING, member_id STRING, claim_id STRING, provider_npi STRING,
  icd10_cm_code STRING, icd10_description STRING, diagnosis_date DATE,
  diagnosis_type STRING, hcc_code STRING, hcc_description STRING,
  raf_weight DOUBLE, chronic_flag BOOLEAN, created_at TIMESTAMP
)
STORED AS PARQUET
LOCATION 's3://{BUCKET}/iceberg/diagnoses/'
TBLPROPERTIES ('parquet.compress'='SNAPPY')""",
    },
    "hedis_gaps": {
        "expected": 400,
        "ddl": f"""CREATE EXTERNAL TABLE IF NOT EXISTS {DATABASE}.hedis_gaps (
  gap_id STRING, member_id STRING, pcp_npi STRING, measure_id STRING,
  measure_name STRING, measure_year INT, gap_status STRING,
  open_date DATE, close_date DATE, exclusion_reason STRING,
  numerator_flag BOOLEAN, denominator_flag BOOLEAN,
  last_service_date DATE, created_at TIMESTAMP
)
STORED AS PARQUET
LOCATION 's3://{BUCKET}/iceberg/hedis_gaps/'
TBLPROPERTIES ('parquet.compress'='SNAPPY')""",
    },
    "risk_scores": {
        "expected": 500,
        "ddl": f"""CREATE EXTERNAL TABLE IF NOT EXISTS {DATABASE}.risk_scores (
  score_id STRING, member_id STRING, score_type STRING, score_date DATE,
  score_period STRING, raf_score DOUBLE, risk_score_value DOUBLE,
  risk_tier STRING, hcc_count INT, top_hcc_code STRING,
  top_hcc_weight DOUBLE, prospective_score DOUBLE, concurrent_score DOUBLE,
  created_at TIMESTAMP
)
STORED AS PARQUET
LOCATION 's3://{BUCKET}/iceberg/risk_scores/'
TBLPROPERTIES ('parquet.compress'='SNAPPY')""",
    },
    "sdoh_barriers": {
        "expected": 200,
        "ddl": f"""CREATE EXTERNAL TABLE IF NOT EXISTS {DATABASE}.sdoh_barriers (
  barrier_id STRING, member_id STRING, barrier_type STRING,
  barrier_category STRING, icd10_z_code STRING, identified_date DATE,
  resolved_date DATE, severity STRING, source STRING,
  referral_made BOOLEAN, referral_org STRING, adi_score INT, created_at TIMESTAMP
)
STORED AS PARQUET
LOCATION 's3://{BUCKET}/iceberg/sdoh_barriers/'
TBLPROPERTIES ('parquet.compress'='SNAPPY')""",
    },
    "pharmacy_claims": {
        "expected": 800,
        "ddl": f"""CREATE EXTERNAL TABLE IF NOT EXISTS {DATABASE}.pharmacy_claims (
  rx_claim_id STRING, member_id STRING, prescriber_npi STRING,
  dispensing_npi STRING, fill_date DATE, ndc_code STRING, drug_name STRING,
  drug_class STRING, rxnorm_code STRING, days_supply INT, quantity DOUBLE,
  refill_number INT, billed_amount DOUBLE, paid_amount DOUBLE,
  generic_flag BOOLEAN, formulary_tier INT, pdc_numerator BOOLEAN,
  high_alert_flag BOOLEAN, created_at TIMESTAMP
)
STORED AS PARQUET
LOCATION 's3://{BUCKET}/iceberg/pharmacy_claims/'
TBLPROPERTIES ('parquet.compress'='SNAPPY')""",
    },
}


def run_query(sql, desc=""):
    resp = athena.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": DATABASE},
        ResultConfiguration={"OutputLocation": OUTPUT},
        WorkGroup=WORKGROUP,
    )
    qid = resp["QueryExecutionId"]
    for _ in range(60):
        time.sleep(2)
        status = athena.get_query_execution(QueryExecutionId=qid)
        state  = status["QueryExecution"]["Status"]["State"]
        if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
    if state != "SUCCEEDED":
        reason = status["QueryExecution"]["Status"].get("StateChangeReason", "")
        raise RuntimeError(f"Query [{desc}] {state}: {reason}")
    return qid


def get_count(qid):
    result = athena.get_query_results(QueryExecutionId=qid)
    return int(result["ResultSet"]["Rows"][1]["Data"][0]["VarCharValue"])


def main():
    passed, failed = [], []

    for table, spec in TABLES.items():
        try:
            run_query(spec["ddl"], f"CREATE {table}")
            print(f"  ✅ DDL applied: {table}")
        except Exception as e:
            print(f"  ⚠️  DDL warning {table}: {e}")

        try:
            qid   = run_query(f"SELECT COUNT(*) FROM {DATABASE}.{table}", f"COUNT {table}")
            count = get_count(qid)
            ok    = count >= spec["expected"]
            mark  = "✅" if ok else "❌"
            print(f"  {mark} {table}: {count} rows (expected ≥{spec['expected']})")
            (passed if ok else failed).append(table)
        except Exception as e:
            print(f"  ❌ COUNT failed {table}: {e}")
            failed.append(table)

    print(f"\n{'='*50}")
    print(f"Validation: {len(passed)}/{len(TABLES)} tables passed")
    if failed:
        print(f"Failed: {failed}")
        sys.exit(1)
    print("All tables validated ✅")


if __name__ == "__main__":
    main()
