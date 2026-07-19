#!/usr/bin/env python3
"""
Generate Bedrock Titan Embeddings v2 (1024d) for:
  1. All ontology concepts from controlled_vocabulary.json
  2. All distinct ICD-10 codes from diagnoses.parquet

Writes JSONL files to:
  - data/sample/embeddings_concepts.jsonl
  - data/sample/embeddings_icd10.jsonl

Also uploads both to s3://vbc-poc-{account}/embeddings/ for later
OpenSearch bulk load when permissions arrive.

Run: python3 load_opensearch_vectors.py
"""
import boto3, json, os, time
import pandas as pd

REGION  = "ap-southeast-2"
ACCOUNT = "020396275984"
BUCKET  = f"vbc-poc-{ACCOUNT}"
MODEL   = "amazon.titan-embed-text-v2:0"
DIMS    = 1024

BASE    = os.path.join(os.path.dirname(__file__), "..", "..")
CV_PATH = os.path.join(BASE, "ontology", "controlled_vocabulary.json")
DX_PATH = os.path.join(BASE, "data", "sample", "diagnoses.parquet")
OUT_DIR = os.path.join(BASE, "data", "sample")

bedrock = boto3.client("bedrock-runtime", region_name=REGION)
s3      = boto3.client("s3",              region_name=REGION)


def embed(text: str) -> list:
    body = json.dumps({"inputText": text, "dimensions": DIMS, "normalize": True})
    resp = bedrock.invoke_model(
        modelId=MODEL,
        body=body,
        contentType="application/json",
        accept="application/json",
    )
    return json.loads(resp["body"].read())["embedding"]


def upload(local_path: str, s3_key: str):
    s3.upload_file(local_path, BUCKET, s3_key)
    print(f"  ⬆️  Uploaded → s3://{BUCKET}/{s3_key}")


# ── 1. Ontology concept embeddings ───────────────────────────────────────────
print("=== Embedding ontology concepts ===")
cv       = json.load(open(CV_PATH))
classes  = cv.get("classes", [])
out_path = os.path.join(OUT_DIR, "embeddings_concepts.jsonl")

with open(out_path, "w") as f:
    for i, cls in enumerate(classes):
        label      = cls.get("label", cls.get("id", ""))
        domain     = cls.get("domain", "")
        definition = cls.get("definition", "")
        alt_labels = ", ".join(cls.get("altLabels", []))

        text = f"{label}: {definition}"
        if domain:
            text += f" Domain: {domain}."
        if alt_labels:
            text += f" Also known as: {alt_labels}."

        try:
            vector = embed(text)
            doc = {
                "class_id":   cls.get("id", f"vbc:{label}"),
                "label":      label,
                "domain":     domain,
                "definition": definition,
                "text":       text,
                "embedding":  vector,
            }
            f.write(json.dumps(doc) + "\n")
            if (i + 1) % 20 == 0 or (i + 1) == len(classes):
                print(f"  [{i+1}/{len(classes)}] embedded: {label}")
            time.sleep(0.1)   # stay within Bedrock TPS
        except Exception as e:
            print(f"  ⚠️  Skipped {label}: {e}")

print(f"  ✅ Concepts JSONL written: {out_path}")
upload(out_path, "embeddings/concepts/embeddings_concepts.jsonl")

# ── 2. ICD-10 code embeddings ────────────────────────────────────────────────
print("\n=== Embedding ICD-10 codes ===")
df_dx    = pd.read_parquet(DX_PATH)
icd_rows = (
    df_dx[["icd10_cm_code", "icd10_description", "hcc_code", "hcc_description"]]
    .drop_duplicates("icd10_cm_code")
    .reset_index(drop=True)
)
out_path2 = os.path.join(OUT_DIR, "embeddings_icd10.jsonl")

with open(out_path2, "w") as f:
    for i, row in icd_rows.iterrows():
        code  = row["icd10_cm_code"]
        desc  = row["icd10_description"]
        hcc   = row["hcc_code"]
        hcc_d = row["hcc_description"]

        text = f"ICD-10 {code}: {desc}."
        if hcc and hcc != "HCC0":
            text += f" Maps to {hcc}: {hcc_d}."

        try:
            vector = embed(text)
            doc = {
                "icd10_code":       code,
                "description":      desc,
                "hcc_code":         hcc,
                "hcc_description":  hcc_d,
                "text":             text,
                "embedding":        vector,
            }
            f.write(json.dumps(doc) + "\n")
            print(f"  [{i+1}/{len(icd_rows)}] {code}: {desc[:50]}")
            time.sleep(0.1)
        except Exception as e:
            print(f"  ⚠️  Skipped {code}: {e}")

print(f"  ✅ ICD-10 JSONL written: {out_path2}")
upload(out_path2, "embeddings/icd10/embeddings_icd10.jsonl")

# ── Summary ───────────────────────────────────────────────────────────────────
concepts_count = sum(1 for _ in open(out_path))
icd10_count    = sum(1 for _ in open(out_path2))
print(f"\n=== Embedding complete ===")
print(f"  Concept embeddings : {concepts_count} docs")
print(f"  ICD-10 embeddings  : {icd10_count} docs")
print(f"  Ready for OpenSearch bulk load once es:* permissions are granted.")
