"""
vbc-proposal-ui Lambda — API Gateway proxy integration.
Routes (all under /poc):
  GET  /ontology/proposals          -> HTML table of proposals
  POST /ontology/approve?id=...     -> mark APPROVED, invoke vbc-ontology-pipeline async
  POST /ontology/reject?id=...&reason=... -> mark REJECTED

GET is also accepted for approve/reject so the plain email links work
from a browser without a form (acceptable for an internal PoC).
"""
import json, os
from datetime import datetime, timezone
import boto3

REGION       = os.environ.get("AWS_REGION", "ap-southeast-2")
TABLE        = os.environ.get("PROPOSALS_TABLE", "vbc-ontology-proposals")
PIPELINE_FN  = os.environ.get("PIPELINE_FUNCTION", "vbc-ontology-pipeline")

dynamodb = boto3.client("dynamodb", region_name=REGION)
lambda_  = boto3.client("lambda", region_name=REGION)


def _html_response(body, status=200):
    return {"statusCode": status, "headers": {"Content-Type": "text/html"},
            "body": body}


def _json_response(body, status=200):
    return {"statusCode": status, "headers": {"Content-Type": "application/json"},
            "body": json.dumps(body)}


def _item_to_dict(item):
    return {k: v.get("S", v.get("N", "")) for k, v in item.items()}


def list_proposals():
    resp = dynamodb.scan(TableName=TABLE)
    items = [_item_to_dict(i) for i in resp.get("Items", [])]
    items.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

    rows = ""
    for p in items:
        badge = {"PENDING": "#e6a700", "APPROVED": "#2e8b57", "REJECTED": "#b22222"}.get(
            p.get("status", ""), "#888")
        actions = ""
        if p.get("status") == "PENDING":
            actions = (f'<a href="approve?id={p["proposal_id"]}">✅ Approve</a> &nbsp; '
                       f'<a href="reject?id={p["proposal_id"]}">❌ Reject</a>')
        rows += f"""<tr>
          <td>{p.get('timestamp','')}</td>
          <td><b>{p.get('term','')}</b></td>
          <td>{p.get('label','')}</td>
          <td>{p.get('suggested_domain','')}</td>
          <td>{p.get('definition','')}</td>
          <td>{p.get('triggering_question','')}</td>
          <td style="color:{badge};font-weight:bold">{p.get('status','')}</td>
          <td>{actions}</td>
        </tr>"""

    html = f"""<!doctype html><html><head><meta charset="utf-8">
    <title>VBC Ontology — Proposed Terms</title>
    <style>
      body {{ font-family: -apple-system, Arial, sans-serif; margin: 2rem; }}
      table {{ border-collapse: collapse; width: 100%; }}
      th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; font-size: 13px; }}
      th {{ background: #f4f4f4; }}
      a {{ text-decoration: none; }}
    </style></head><body>
    <h2>VBC Ontology — Proposed Terms ({len(items)})</h2>
    <table>
      <tr><th>Time</th><th>Term</th><th>Label</th><th>Domain</th><th>Definition</th>
          <th>Triggering question</th><th>Status</th><th>Action</th></tr>
      {rows}
    </table>
    </body></html>"""
    return _html_response(html)


def approve(proposal_id):
    if not proposal_id:
        return _html_response("<p>Missing id</p>", 400)
    now = datetime.now(timezone.utc).isoformat()
    try:
        dynamodb.update_item(
            TableName=TABLE,
            Key={"proposal_id": {"S": proposal_id}},
            UpdateExpression="SET #s = :s, review_timestamp = :t",
            ConditionExpression="#s = :pending",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":s": {"S": "APPROVING"}, ":t": {"S": now}, ":pending": {"S": "PENDING"}},
        )
    except dynamodb.exceptions.ConditionalCheckFailedException:
        return _html_response("<p>Proposal is not PENDING (already reviewed).</p>", 409)
    except Exception as e:
        return _html_response(f"<p>Could not update proposal: {e}</p>", 500)

    try:
        lambda_.invoke(FunctionName=PIPELINE_FN, InvocationType="Event",
                        Payload=json.dumps({"proposal_id": proposal_id}).encode())
    except Exception as e:
        return _html_response(f"<p>Marked APPROVING but failed to start pipeline: {e}</p>", 500)

    return _html_response(f"<p>✅ Approved. Ontology pipeline started for proposal {proposal_id}. "
                           f"<a href='proposals'>Back to list</a></p>")


def reject(proposal_id, reason):
    if not proposal_id:
        return _html_response("<p>Missing id</p>", 400)
    now = datetime.now(timezone.utc).isoformat()
    try:
        dynamodb.update_item(
            TableName=TABLE,
            Key={"proposal_id": {"S": proposal_id}},
            UpdateExpression="SET #s = :s, review_timestamp = :t, rejection_reason = :r",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":s": {"S": "REJECTED"}, ":t": {"S": now}, ":r": {"S": reason or ""}},
        )
    except Exception as e:
        return _html_response(f"<p>Could not update proposal: {e}</p>", 500)
    return _html_response(f"<p>❌ Rejected proposal {proposal_id}. <a href='proposals'>Back to list</a></p>")


def lambda_handler(event, context):
    path = event.get("path") or event.get("rawPath") or ""
    qs   = event.get("queryStringParameters") or {}

    try:
        if path.endswith("/proposals"):
            return list_proposals()
        if path.endswith("/approve"):
            return approve(qs.get("id"))
        if path.endswith("/reject"):
            return reject(qs.get("id"), qs.get("reason"))
        return _json_response({"error": "not found", "path": path}, 404)
    except Exception as e:
        return _json_response({"error": str(e)[:300]}, 500)
