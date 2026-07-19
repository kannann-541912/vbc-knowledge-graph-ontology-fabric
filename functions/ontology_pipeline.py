"""
vbc-ontology-pipeline Lambda — the core Phase C update.
Invoked asynchronously (Event) by vbc-proposal-ui when a proposal is approved.
Payload: {"proposal_id": "..."}

Steps:
 1. Load proposal from DynamoDB
 2. Load controlled_vocabulary.json from S3, append new class, re-upload
 3. Append new class triples to taxonomy.ttl and vbc_ontology.owl in S3
 4. INSERT DATA the new class into Neptune (via vbc-sparql-relay)
 5. Embed the new class (Titan v2) and index into vbc-concepts-embeddings
 6. Append an entry to ontology/governance/change_log.md in S3
 7. Run CQ regression (via vbc-cq-regression)
 8. Mark the proposal APPROVED (or REGRESSION_DETECTED) in DynamoDB
 9. Best-effort SNS notification of the outcome
"""
import json, os, base64, urllib.request
from datetime import datetime, timezone
import boto3

REGION        = os.environ.get("AWS_REGION", "ap-southeast-2")
BUCKET        = os.environ.get("ONTOLOGY_BUCKET", "vbc-poc-020396275984")
TABLE         = os.environ.get("PROPOSALS_TABLE", "vbc-ontology-proposals")
RELAY_FN      = os.environ.get("SPARQL_RELAY_FUNCTION", "vbc-sparql-relay")
CQ_FN         = os.environ.get("CQ_REGRESSION_FUNCTION", "vbc-cq-regression")
TOPIC_ARN     = os.environ.get("TOPIC_ARN", "")
OS_ENDPOINT   = os.environ.get("OPENSEARCH_ENDPOINT",
    "search-vbc-vectors-poc-nuxmojpv6rtsiywdxpw53pq6tm.ap-southeast-2.es.amazonaws.com")
OS_USER       = os.environ.get("OPENSEARCH_USER", "vbcadmin")
OS_PASS       = os.environ.get("OPENSEARCH_PASS", "VbcPoc2024!")
OS_INDEX      = os.environ.get("OPENSEARCH_CONCEPTS_INDEX", "vbc-concepts-embeddings")
EMBED_MODEL   = os.environ.get("EMBED_MODEL", "amazon.titan-embed-text-v2:0")
VBC_NS        = "https://ontology.vbc.internal/vbc#"

dynamodb = boto3.client("dynamodb", region_name=REGION)
s3       = boto3.client("s3", region_name=REGION)
lambda_  = boto3.client("lambda", region_name=REGION)
bedrock  = boto3.client("bedrock-runtime", region_name=REGION)
sns      = boto3.client("sns", region_name=REGION)


def get_proposal(proposal_id):
    resp = dynamodb.get_item(TableName=TABLE, Key={"proposal_id": {"S": proposal_id}})
    item = resp.get("Item")
    if not item:
        raise ValueError(f"proposal {proposal_id} not found")
    return {k: v.get("S", "") for k, v in item.items()}


def s3_get_text(key, default=""):
    try:
        return s3.get_object(Bucket=BUCKET, Key=key)["Body"].read().decode()
    except s3.exceptions.NoSuchKey:
        return default
    except Exception:
        return default


def s3_put_text(key, text):
    s3.put_object(Bucket=BUCKET, Key=key, Body=text.encode())


def update_controlled_vocabulary(proposal):
    key = "ontology/controlled_vocabulary.json"
    vocab = json.loads(s3_get_text(key, "{}") or "{}")
    vocab.setdefault("classes", [])
    class_id = f"vbc:{proposal['term']}"
    if any(c.get("id") == class_id for c in vocab["classes"]):
        return vocab  # already present, idempotent re-run
    vocab["classes"].append({
        "id": class_id,
        "label": proposal["label"],
        "domain": proposal["suggested_domain"],
        "definition": proposal["definition"],
        "subClassOf": proposal["suggested_parent"],
    })
    vocab.setdefault("stats", {})
    vocab["stats"]["classes"] = len(vocab["classes"])
    s3_put_text(key, json.dumps(vocab, indent=2))
    return vocab


def append_taxonomy(proposal):
    key = "ontology/taxonomy.ttl"
    existing = s3_get_text(key,
        "@prefix vbc:  <https://ontology.vbc.internal/vbc#> .\n"
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
        "@prefix owl:  <http://www.w3.org/2002/07/owl#> .\n\n")
    short = proposal["term"]
    defn = proposal["definition"].replace('"', "'")
    parent_short = proposal["suggested_parent"].replace("vbc:", "")
    block = (f'\nvbc:{short} a owl:Class ;\n'
             f'    rdfs:label "{proposal["label"]}" ;\n'
             f'    rdfs:comment "{defn}" ;\n'
             f'    vbc:domain "{proposal["suggested_domain"]}" ;\n'
             f'    rdfs:subClassOf vbc:{parent_short} .\n')
    if f"vbc:{short} a owl:Class" in existing:
        return
    s3_put_text(key, existing + block)


def append_owl(proposal):
    key = "ontology/vbc_ontology.owl"
    existing = s3_get_text(key, "")
    short = proposal["term"]
    if not existing or f'vbc#{short}"' in existing:
        return  # no base file yet, or already appended
    defn = (proposal["definition"].replace("&", "&amp;")
            .replace("<", "&lt;").replace('"', "&quot;"))
    parent_short = proposal["suggested_parent"].replace("vbc:", "")
    block = (f'\n  <owl:Class rdf:about="https://ontology.vbc.internal/vbc#{short}">\n'
             f'    <rdfs:label>{proposal["label"]}</rdfs:label>\n'
             f'    <rdfs:comment>{defn}</rdfs:comment>\n'
             f'    <rdfs:subClassOf rdf:resource="https://ontology.vbc.internal/vbc#{parent_short}"/>\n'
             f'  </owl:Class>\n')
    if "</rdf:RDF>" in existing:
        updated = existing.replace("</rdf:RDF>", block + "</rdf:RDF>")
    else:
        updated = existing + block
    s3_put_text(key, updated)


def insert_neptune_triple(proposal):
    short = proposal["term"]
    defn = proposal["definition"].replace('"', "'").replace("\\", "\\\\")
    label = proposal["label"].replace('"', "'")
    parent_short = proposal["suggested_parent"].replace("vbc:", "")
    sparql = f"""
    PREFIX vbc: <{VBC_NS}>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    PREFIX owl:  <http://www.w3.org/2002/07/owl#>
    INSERT DATA {{
      GRAPH <https://ontology.vbc.internal/vbc/schema> {{
        vbc:{short} a owl:Class ;
          rdfs:label "{label}" ;
          rdfs:subClassOf vbc:{parent_short} ;
          rdfs:comment "{defn}" .
      }}
    }}
    """
    resp = lambda_.invoke(
        FunctionName=RELAY_FN, InvocationType="RequestResponse",
        Payload=json.dumps({"type": "update", "sparql": sparql}).encode(),
    )
    payload = json.loads(resp["Payload"].read())
    status = payload.get("statusCode", 0)
    if status not in (200, 204):
        raise RuntimeError(f"Neptune insert failed: {payload}")


def embed(text):
    resp = bedrock.invoke_model(
        modelId=EMBED_MODEL,
        body=json.dumps({"inputText": text, "dimensions": 1024}),
        contentType="application/json", accept="application/json",
    )
    return json.loads(resp["body"].read())["embedding"]


def index_opensearch(proposal):
    class_id = f"vbc:{proposal['term']}"
    text = f"{proposal['label']}: {proposal['definition']}"
    vector = embed(text)
    doc = {
        "class_id": class_id,
        "label": proposal["label"],
        "domain": proposal["suggested_domain"],
        "definition": proposal["definition"],
        "embedding": vector,
    }
    creds = base64.b64encode(f"{OS_USER}:{OS_PASS}".encode()).decode()
    doc_id = proposal["term"]
    req = urllib.request.Request(
        f"https://{OS_ENDPOINT}/{OS_INDEX}/_doc/{doc_id}",
        data=json.dumps(doc).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Basic {creds}"},
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        r.read()


def append_change_log(proposal, proposal_id, cq_result):
    key = "ontology/governance/change_log.md"
    existing = s3_get_text(key, "# VBC Ontology Change Log\n")
    entry = (f"\n## {datetime.now(timezone.utc).strftime('%Y-%m-%d')} — ADD {proposal['term']}\n"
              f"- **Change type:** New class\n"
              f"- **Term:** `vbc:{proposal['term']}` (rdfs:subClassOf {proposal['suggested_parent']})\n"
              f"- **Domain:** {proposal['suggested_domain']}\n"
              f"- **Definition:** {proposal['definition']}\n"
              f"- **Triggered by:** agent question — \"{proposal['triggering_question']}\"\n"
              f"- **Proposal ID:** {proposal_id}\n"
              f"- **CQ re-run:** {'ALL PASSED' if not cq_result.get('regression_detected') else 'REGRESSION DETECTED'}\n")
    s3_put_text(key, existing + entry)


def run_cq_regression():
    try:
        resp = lambda_.invoke(FunctionName=CQ_FN, InvocationType="RequestResponse", Payload=b"{}")
        return json.loads(resp["Payload"].read())
    except Exception as e:
        return {"regression_detected": None, "error": str(e)[:200]}


def mark_status(proposal_id, status, extra=None):
    expr_names = {"#s": "status"}
    expr_values = {":s": {"S": status}}
    set_clause = "SET #s = :s"
    if extra:
        for i, (k, v) in enumerate(extra.items()):
            expr_names[f"#e{i}"] = k
            expr_values[f":e{i}"] = {"S": str(v)}
            set_clause += f", #e{i} = :e{i}"
    dynamodb.update_item(
        TableName=TABLE, Key={"proposal_id": {"S": proposal_id}},
        UpdateExpression=set_clause,
        ExpressionAttributeNames=expr_names,
        ExpressionAttributeValues=expr_values,
    )


def notify(subject, message):
    if not TOPIC_ARN:
        print(f"[ontology_pipeline] no TOPIC_ARN, skipping SNS: {subject}")
        return
    try:
        sns.publish(TopicArn=TOPIC_ARN, Subject=subject[:100], Message=message)
    except Exception as e:
        print(f"[ontology_pipeline] SNS publish failed: {e}")


def lambda_handler(event, context):
    proposal_id = event.get("proposal_id")
    if not proposal_id:
        return {"status": "error", "message": "proposal_id required"}

    try:
        proposal = get_proposal(proposal_id)
    except Exception as e:
        print(f"[ontology_pipeline] {e}")
        return {"status": "error", "message": str(e)}

    steps_done = []
    try:
        update_controlled_vocabulary(proposal)
        steps_done.append("controlled_vocabulary.json")

        append_taxonomy(proposal)
        steps_done.append("taxonomy.ttl")

        append_owl(proposal)
        steps_done.append("vbc_ontology.owl")

        insert_neptune_triple(proposal)
        steps_done.append("neptune_insert")

        try:
            index_opensearch(proposal)
            steps_done.append("opensearch_index")
        except Exception as e:
            print(f"[ontology_pipeline] opensearch indexing failed (non-fatal): {e}")

        cq_result = run_cq_regression()
        steps_done.append("cq_regression")

        append_change_log(proposal, proposal_id, cq_result)
        steps_done.append("change_log.md")

        final_status = "REGRESSION_DETECTED" if cq_result.get("regression_detected") else "APPROVED"
        mark_status(proposal_id, final_status, {"pipeline_steps": ",".join(steps_done)})

        notify(
            f"[VBC Ontology] {final_status} — {proposal['term']}",
            f"Ontology update pipeline finished for '{proposal['term']}'.\n"
            f"Status: {final_status}\nSteps completed: {steps_done}\n"
            f"CQ regression: {json.dumps(cq_result)}",
        )
        return {"status": final_status, "steps_done": steps_done, "cq_result": cq_result}

    except Exception as e:
        print(f"[ontology_pipeline] FAILED at step after {steps_done}: {e}")
        mark_status(proposal_id, "PIPELINE_FAILED", {"error": str(e)[:200], "pipeline_steps": ",".join(steps_done)})
        notify(f"[VBC Ontology] PIPELINE FAILED — {proposal.get('term','?')}",
               f"Failed after steps: {steps_done}\nError: {e}")
        return {"status": "error", "steps_done": steps_done, "error": str(e)}
