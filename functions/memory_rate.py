"""
vbc-memory-rate Lambda  (NOT in VPC)
Handles POST /rate from API Gateway. Updates the rating field for a stored
query in both DynamoDB (source of truth) and the OpenSearch memory index
(so future KNN retrieval can boost highly-rated queries).
"""
import json, os, boto3, base64, urllib.request

REGION      = os.environ.get("AWS_REGION", "ap-southeast-2")
TABLE_NAME  = os.environ.get("MEMORY_TABLE", "vbc-query-memory")
OS_ENDPOINT = os.environ.get("OPENSEARCH_ENDPOINT",
                              "search-vbc-vectors-poc-nuxmojpv6rtsiywdxpw53pq6tm.ap-southeast-2.es.amazonaws.com")
OS_USER     = os.environ.get("OPENSEARCH_USER", "vbcadmin")
OS_PASS     = os.environ.get("OPENSEARCH_PASS", "VbcPoc2024!")
OS_INDEX    = os.environ.get("OPENSEARCH_MEMORY_INDEX", "vbc-query-memory")

dynamodb = boto3.client("dynamodb", region_name=REGION)

def os_update_rating(doc_id: str, rating: float):
    creds = base64.b64encode(f"{OS_USER}:{OS_PASS}".encode()).decode()
    payload = json.dumps({"doc": {"rating": rating}}).encode()
    req = urllib.request.Request(
        f"https://{OS_ENDPOINT}/{OS_INDEX}/_update/{doc_id}",
        data=payload, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Basic {creds}"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())

def lambda_handler(event, context):
    try:
        body = (json.loads(event.get("body", "{}"))
                if isinstance(event.get("body"), str) else event)
        query_id = body.get("query_id", "").strip()
        rating   = body.get("rating")
        comment  = body.get("comment", "")

        if not query_id or rating is None:
            return {"statusCode": 400, "body": json.dumps({"error": "Missing 'query_id' or 'rating'"})}

        rating = float(rating)

        resp = dynamodb.query(
            TableName=TABLE_NAME,
            KeyConditionExpression="query_id = :qid",
            ExpressionAttributeValues={":qid": {"S": query_id}},
            ScanIndexForward=False,
            Limit=1,
        )
        items = resp.get("Items", [])
        if not items:
            return {"statusCode": 404, "body": json.dumps({"error": f"query_id {query_id} not found"})}

        timestamp = items[0]["timestamp"]["S"]

        update_expr = "SET rating = :r"
        expr_values = {":r": {"N": str(rating)}}
        if comment:
            update_expr += ", comment = :c"
            expr_values[":c"] = {"S": comment}

        dynamodb.update_item(
            TableName=TABLE_NAME,
            Key={"query_id": {"S": query_id}, "timestamp": {"S": timestamp}},
            UpdateExpression=update_expr,
            ExpressionAttributeValues=expr_values,
        )

        os_update_rating(query_id, rating)

        return {"statusCode": 200, "body": json.dumps({"updated": True, "query_id": query_id, "rating": rating})}
    except Exception as e:
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}
