"""
get_patient_360 Lambda
Returns a full 360 view of a patient by querying Athena tables.
Invoked by the Bedrock Agent action group.
"""
import json, os, boto3, time

REGION   = os.environ.get("AWS_REGION", "ap-southeast-2")
DATABASE = os.environ.get("GLUE_DATABASE", "vbc_poc_db")
S3_OUT   = os.environ.get("ATHENA_OUTPUT", "s3://vbc-poc-020396275984/raw/athena-results/")

athena = boto3.client("athena", region_name=REGION)


def run_query(sql: str) -> list:
    resp = athena.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": DATABASE},
        ResultConfiguration={"OutputLocation": S3_OUT},
    )
    qid = resp["QueryExecutionId"]
    for _ in range(30):
        time.sleep(1)
        status = athena.get_query_execution(QueryExecutionId=qid)
        state  = status["QueryExecution"]["Status"]["State"]
        if state == "SUCCEEDED":
            break
        if state in ("FAILED", "CANCELLED"):
            raise RuntimeError(f"Athena query {state}: "
                               + status["QueryExecution"]["Status"].get("StateChangeReason", ""))
    result  = athena.get_query_results(QueryExecutionId=qid)
    rows    = result["ResultSet"]["Rows"]
    if len(rows) < 2:
        return []
    headers = [c["VarCharValue"] for c in rows[0]["Data"]]
    return [
        {headers[i]: col.get("VarCharValue", "") for i, col in enumerate(r["Data"])}
        for r in rows[1:]
    ]


def lambda_handler(event, context):
    # Support both direct invoke and Bedrock Agent action group format
    props = event.get("requestBody", {}).get("content", {}).get(
        "application/json", {}).get("properties", [])
    member_id = ""
    for p in props:
        if p.get("name") == "member_id":
            member_id = p.get("value", "").strip()

    # Fallback: direct invoke with {"member_id": "M-0042"}
    if not member_id:
        member_id = event.get("member_id", "").strip()

    if not member_id:
        return _agent_response(event, {"error": "member_id is required"}, 400)

    # Normalize: accept "M-0042" or "0042"
    if not member_id.startswith("M-"):
        member_id = f"M-{member_id.zfill(4)}"

    try:
        member = run_query(
            f"SELECT * FROM member_master WHERE member_id = '{member_id}' LIMIT 1")
        diagnoses = run_query(
            f"SELECT icd10_cm_code, diagnosis_date, hcc_category "
            f"FROM diagnosis_history WHERE member_id = '{member_id}' "
            f"ORDER BY diagnosis_date DESC LIMIT 10")
        gaps = run_query(
            f"SELECT measure_name, gap_status, gap_open_date, gap_close_date "
            f"FROM hedis_gaps WHERE member_id = '{member_id}'")
        risk = run_query(
            f"SELECT risk_tier, composite_risk_score, hcc_raf_score "
            f"FROM risk_scores WHERE member_id = '{member_id}' LIMIT 1")
        sdoh = run_query(
            f"SELECT barrier_type, barrier_severity, identified_date "
            f"FROM sdoh_barriers WHERE member_id = '{member_id}'")

        result = {
            "member_id":   member_id,
            "demographics": member[0] if member else {},
            "diagnoses":   diagnoses,
            "care_gaps":   gaps,
            "risk_score":  risk[0] if risk else {},
            "sdoh_barriers": sdoh,
        }
        return _agent_response(event, result, 200)

    except Exception as e:
        return _agent_response(event, {"error": str(e)}, 500)


def _agent_response(event, body: dict, status: int) -> dict:
    # Return format for Bedrock Agent action group
    return {
        "messageVersion": "1.0",
        "response": {
            "actionGroup":  event.get("actionGroup", "get_patient_360"),
            "apiPath":      event.get("apiPath", "/patient360"),
            "httpMethod":   event.get("httpMethod", "POST"),
            "httpStatusCode": status,
            "responseBody": {
                "application/json": {"body": json.dumps(body)}
            },
        },
    }
