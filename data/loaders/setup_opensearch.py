#!/usr/bin/env python3
"""
Create OpenSearch KNN indices and bulk-load embeddings from S3 JSONL files.
Uses AWS Signature v4 (boto3 credentials) for all requests.

Run: python3 setup_opensearch.py
"""
import boto3, json, os, sys
import requests
from requests.auth import HTTPBasicAuth

REGION   = "ap-southeast-2"
ACCOUNT  = "020396275984"
BUCKET   = f"vbc-poc-{ACCOUNT}"
OS_HOST  = "search-vbc-vectors-poc-nuxmojpv6rtsiywdxpw53pq6tm.ap-southeast-2.es.amazonaws.com"
BASE_URL = f"https://{OS_HOST}"
OS_AUTH  = HTTPBasicAuth("vbcadmin", "VbcPoc2024!")   # master user — full permissions

BASE    = os.path.join(os.path.dirname(__file__), "..", "..")
SAMPLE  = os.path.join(BASE, "data", "sample")

def os_request(method, path, body=None):
    url  = f"{BASE_URL}{path}"
    hdrs = {"Content-Type": "application/json"}
    resp = requests.request(method, url, auth=OS_AUTH, headers=hdrs,
                            json=body, timeout=30)
    return resp


# ── Index definitions ─────────────────────────────────────────────────────────
INDICES = {
    "vbc-concepts-embeddings": {
        "settings": {"index": {"knn": True, "knn.algo_param.ef_search": 100,
                               "number_of_shards": 1, "number_of_replicas": 0}},
        "mappings": {"properties": {
            "class_id":   {"type": "keyword"},
            "label":      {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
            "domain":     {"type": "keyword"},
            "definition": {"type": "text"},
            "text":       {"type": "text"},
            "embedding":  {"type": "knn_vector", "dimension": 1024,
                           "method": {"name": "hnsw", "space_type": "cosinesimil",
                                      "engine": "nmslib"}},
        }},
    },
    "vbc-icd10-embeddings": {
        "settings": {"index": {"knn": True, "knn.algo_param.ef_search": 100,
                               "number_of_shards": 1, "number_of_replicas": 0}},
        "mappings": {"properties": {
            "icd10_code":      {"type": "keyword"},
            "description":     {"type": "text"},
            "hcc_code":        {"type": "keyword"},
            "hcc_description": {"type": "text"},
            "text":            {"type": "text"},
            "embedding":       {"type": "knn_vector", "dimension": 1024,
                                "method": {"name": "hnsw", "space_type": "cosinesimil",
                                           "engine": "nmslib"}},
        }},
    },
    "vbc-clinical-notes": {
        "settings": {"index": {"knn": True,
                               "number_of_shards": 1, "number_of_replicas": 0}},
        "mappings": {"properties": {
            "note_id":   {"type": "keyword"},
            "member_id": {"type": "keyword"},
            "note_text": {"type": "text"},
            "note_date": {"type": "date"},
            "embedding": {"type": "knn_vector", "dimension": 1024,
                          "method": {"name": "hnsw", "space_type": "cosinesimil",
                                     "engine": "nmslib"}},
        }},
    },
}


def create_indices():
    print("=== Creating OpenSearch indices ===")
    for name, body in INDICES.items():
        # Delete if exists (clean slate)
        os_request("DELETE", f"/{name}")
        resp = os_request("PUT", f"/{name}", body)
        if resp.status_code in (200, 201):
            print(f"  ✅ Created index: {name}")
        else:
            print(f"  ❌ Failed {name}: {resp.status_code} — {resp.text[:200]}")
            sys.exit(1)


def bulk_load(index_name, jsonl_path, id_field):
    print(f"\n  Loading {os.path.basename(jsonl_path)} → {index_name}...")
    docs = [json.loads(line) for line in open(jsonl_path) if line.strip()]
    bulk_body = ""
    for i, doc in enumerate(docs):
        doc_id = doc.get(id_field, str(i)).replace("vbc:", "").replace(":", "_")
        bulk_body += json.dumps({"index": {"_index": index_name, "_id": doc_id}}) + "\n"
        bulk_body += json.dumps(doc) + "\n"

    resp = requests.post(
        f"{BASE_URL}/_bulk",
        auth=OS_AUTH,
        headers={"Content-Type": "application/x-ndjson"},
        data=bulk_body,
        timeout=60,
    )
    if resp.status_code == 200:
        result = resp.json()
        errors = result.get("errors", False)
        items  = result.get("items", [])
        ok     = sum(1 for i in items if i.get("index", {}).get("status") in (200, 201))
        mark   = "✅" if not errors else "⚠️ "
        print(f"  {mark} {index_name}: {ok}/{len(docs)} docs loaded")
        return ok
    else:
        print(f"  ❌ Bulk load failed: {resp.status_code} — {resp.text[:300]}")
        return 0


def validate_search(index_name, query_text, expected_top_id, id_field):
    """Semantic similarity search validation."""
    import boto3 as b3
    br = b3.client("bedrock-runtime", region_name=REGION)
    body = json.dumps({"inputText": query_text, "dimensions": 1024, "normalize": True})
    resp = br.invoke_model(modelId="amazon.titan-embed-text-v2:0", body=body,
                           contentType="application/json", accept="application/json")
    vec = json.loads(resp["body"].read())["embedding"]

    search_body = {
        "size": 1,
        "query": {"knn": {"embedding": {"vector": vec, "k": 1}}},
        "_source": [id_field, "label", "description", "icd10_code"],
    }
    r = requests.post(f"{BASE_URL}/{index_name}/_search",
                      auth=OS_AUTH, headers={"Content-Type": "application/json"},
                      json=search_body, timeout=15)
    if r.status_code == 200:
        hits = r.json().get("hits", {}).get("hits", [])
        if hits:
            top = hits[0]["_source"]
            top_id = top.get(id_field, "")
            match = expected_top_id.lower() in str(top_id).lower()
            mark = "✅" if match else "⚠️ "
            print(f"  {mark} '{query_text}' → top result: {top_id} (expected: {expected_top_id})")
            return match
    print(f"  ❌ Search failed for '{query_text}': {r.status_code}")
    return False


def main():
    create_indices()

    print("\n=== Bulk loading embeddings ===")
    c1 = bulk_load("vbc-concepts-embeddings",
                   os.path.join(SAMPLE, "embeddings_concepts.jsonl"), "class_id")
    c2 = bulk_load("vbc-icd10-embeddings",
                   os.path.join(SAMPLE, "embeddings_icd10.jsonl"), "icd10_code")

    print("\n=== Vector similarity validation ===")
    validate_search("vbc-icd10-embeddings", "congestive heart failure", "I50.9", "icd10_code")
    validate_search("vbc-icd10-embeddings", "sugar diabetes",           "E11.9",  "icd10_code")
    validate_search("vbc-concepts-embeddings", "care gap quality measure", "CareGap", "class_id")

    print(f"\n=== Summary ===")
    print(f"  vbc-concepts-embeddings : {c1} docs")
    print(f"  vbc-icd10-embeddings    : {c2} docs")

    if c1 >= 130 and c2 >= 6:
        print("\n✅ Phase 4 validation gates passed.")
    else:
        print("\n❌ Validation gates failed — check counts above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
