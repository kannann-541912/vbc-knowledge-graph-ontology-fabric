"""
vbc-stream-notifier Lambda — triggered by DynamoDB Streams on
vbc-ontology-proposals (NEW_IMAGE). Sends an SNS email for every new
PENDING proposal with approve/reject links.

Degrades gracefully: if SNS isn't reachable/authorized, logs and returns
success so the stream checkpoint still advances (a stuck stream would
otherwise replay the same record forever).
"""
import json, os
import boto3

REGION     = os.environ.get("AWS_REGION", "ap-southeast-2")
TOPIC_ARN  = os.environ.get("TOPIC_ARN", "")
API_BASE   = os.environ.get("API_BASE", "https://trzyzra8ve.execute-api.ap-southeast-2.amazonaws.com/poc")

sns = boto3.client("sns", region_name=REGION)


def _s(image, key, default=""):
    return image.get(key, {}).get("S", default)


def lambda_handler(event, context):
    sent, skipped, errors = 0, 0, 0

    for record in event.get("Records", []):
        if record.get("eventName") != "INSERT":
            continue
        new_image = record.get("dynamodb", {}).get("NewImage", {})
        if _s(new_image, "status") != "PENDING":
            skipped += 1
            continue

        proposal_id = _s(new_image, "proposal_id")
        term        = _s(new_image, "term")
        label       = _s(new_image, "label", term)
        definition  = _s(new_image, "definition")
        domain      = _s(new_image, "suggested_domain")
        question    = _s(new_image, "triggering_question")

        approve_url = f"{API_BASE}/ontology/approve?id={proposal_id}"
        reject_url  = f"{API_BASE}/ontology/reject?id={proposal_id}"
        list_url    = f"{API_BASE}/ontology/proposals"

        message = f"""New ontology term proposed by VBC Care Navigator agent:

Term:        {term}
Label:       {label}
Domain:      {domain}
Definition:  {definition}
Triggered by question: "{question}"

APPROVE: {approve_url}
REJECT:  {reject_url}

Or review all pending proposals: {list_url}
"""
        if not TOPIC_ARN:
            print(f"[stream_notifier] TOPIC_ARN not configured, would have sent: {term}")
            skipped += 1
            continue

        try:
            sns.publish(
                TopicArn=TOPIC_ARN,
                Subject=f"[VBC Ontology] New term proposed: {term}"[:100],
                Message=message,
            )
            sent += 1
        except Exception as e:
            print(f"[stream_notifier] SNS publish failed for {term}: {e}")
            errors += 1

    return {"sent": sent, "skipped": skipped, "errors": errors}
