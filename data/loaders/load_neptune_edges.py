#!/usr/bin/env python3
"""
Load all VBC relationship edges into Neptune SPARQL endpoint.
Must run AFTER load_neptune_nodes.py — nodes must exist first.

Edges loaded:
  Patient → hasDiagnosis      → PatientDiagnosis
  Patient → hasCareGap        → CareGap
  Patient → hasRiskScore      → RiskFactor
  Patient → hasSDOHBarrier    → SDOHBarrier
  Patient → hasPCP            → PrimaryCarePhysician

Usage: python3 load_neptune_edges.py
"""
import os
import pandas as pd
import requests, urllib3
urllib3.disable_warnings()

NEPTUNE_HOST = "vbc-neptune-poc.cluster-cxe0k4i6swp1.ap-southeast-2.neptune.amazonaws.com"
NEPTUNE_PORT = 8182
SPARQL_URL   = f"https://{NEPTUNE_HOST}:{NEPTUNE_PORT}/sparql"
VBC_NS       = "https://ontology.vbc.internal/vbc#"
GRAPH        = "https://ontology.vbc.internal/vbc/instances"
BATCH_SIZE   = 80

BASE   = os.path.join(os.path.dirname(__file__), "..", "..")
SAMPLE = os.path.join(BASE, "data", "sample")


def sparql_update(query, label=""):
    resp = requests.post(
        SPARQL_URL,
        data={"update": query},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        verify=False, timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"[{label}] HTTP {resp.status_code}: {resp.text[:300]}")


def build_insert(triples):
    return f"""PREFIX vbc: <{VBC_NS}>
INSERT DATA {{
  GRAPH <{GRAPH}> {{
    {chr(10) + "    ".join(triples)}
  }}
}}"""


def flush(triples, label, loaded, errors):
    if not triples:
        return loaded, errors
    try:
        sparql_update(build_insert(triples), label)
        return loaded + len(triples), errors
    except Exception as e:
        print(f"  ⚠️  Batch error [{label}]: {e}")
        return loaded, errors + 1


def load_edges(pairs, label):
    triples, loaded, errors = [], 0, 0
    for t in pairs:
        if t:
            triples.append(t)
        if len(triples) >= BATCH_SIZE:
            loaded, errors = flush(triples, label, loaded, errors)
            triples = []
    loaded, errors = flush(triples, label, loaded, errors)
    mark = "✅" if errors == 0 else "⚠️ "
    print(f"  {mark} {label}: {loaded} edges ({errors} errors)")
    return loaded


def esc(val):
    if val is None: return None
    s = str(val)
    return None if s in ("nan", "NaT", "None", "") else s


def main():
    print("=== Loading Neptune edges ===\n")
    total = 0

    # ── Patient → hasDiagnosis → PatientDiagnosis ─────────────────────────────
    df_dx = pd.read_parquet(os.path.join(SAMPLE, "diagnoses.parquet"))
    print(f"  Patient→hasDiagnosis ({len(df_dx)} rows)...")
    total += load_edges(
        (f'vbc:Patient_{esc(r.member_id)} vbc:hasDiagnosis vbc:PatientDiagnosis_{esc(r.diagnosis_id)} .'
         if esc(r.member_id) and esc(r.diagnosis_id) else None
         for r in df_dx.itertuples(index=False)),
        "hasDiagnosis"
    )

    # ── Patient → hasCareGap → CareGap ───────────────────────────────────────
    df_gaps = pd.read_parquet(os.path.join(SAMPLE, "hedis_gaps.parquet"))
    print(f"  Patient→hasCareGap ({len(df_gaps)} rows)...")
    total += load_edges(
        (f'vbc:Patient_{esc(r.member_id)} vbc:hasCareGap vbc:CareGap_{esc(r.gap_id)} .'
         if esc(r.member_id) and esc(r.gap_id) else None
         for r in df_gaps.itertuples(index=False)),
        "hasCareGap"
    )

    # ── Patient → hasRiskScore → RiskFactor ───────────────────────────────────
    df_rs = pd.read_parquet(os.path.join(SAMPLE, "risk_scores.parquet"))
    print(f"  Patient→hasRiskScore ({len(df_rs)} rows)...")
    total += load_edges(
        (f'vbc:Patient_{esc(r.member_id)} vbc:hasRiskScore vbc:RiskFactor_{esc(r.score_id)} .'
         if esc(r.member_id) and esc(r.score_id) else None
         for r in df_rs.itertuples(index=False)),
        "hasRiskScore"
    )

    # ── Patient → hasSDOHBarrier → SDOHBarrier ────────────────────────────────
    df_sdoh = pd.read_parquet(os.path.join(SAMPLE, "sdoh_barriers.parquet"))
    print(f"  Patient→hasSDOHBarrier ({len(df_sdoh)} rows)...")
    total += load_edges(
        (f'vbc:Patient_{esc(r.member_id)} vbc:hasSDOHBarrier vbc:SDOHBarrier_{esc(r.barrier_id)} .'
         if esc(r.member_id) and esc(r.barrier_id) else None
         for r in df_sdoh.itertuples(index=False)),
        "hasSDOHBarrier"
    )

    # ── Patient → hasPCP → PrimaryCarePhysician ───────────────────────────────
    df_mem = pd.read_parquet(os.path.join(SAMPLE, "members.parquet"))
    print(f"  Patient→hasPCP ({len(df_mem)} rows)...")
    total += load_edges(
        (f'vbc:Patient_{esc(r.member_id)} vbc:hasPCP vbc:PrimaryCarePhysician_{esc(r.pcp_npi)} .'
         if esc(r.member_id) and esc(r.pcp_npi) else None
         for r in df_mem.itertuples(index=False)),
        "hasPCP"
    )

    # ── PatientDiagnosis → diagnosedWith → mapsToHCC ─────────────────────────
    print(f"  PatientDiagnosis→hccCode ({len(df_dx)} rows)...")
    total += load_edges(
        (f'vbc:PatientDiagnosis_{esc(r.diagnosis_id)} vbc:forMember vbc:Patient_{esc(r.member_id)} .'
         if esc(r.diagnosis_id) and esc(r.member_id) else None
         for r in df_dx.itertuples(index=False)),
        "PatientDiagnosis→forMember"
    )

    print(f"\n✅ Total edge triples loaded: {total}")
    print(f"\n--- Node + edge summary ---")
    print(f"  Patient nodes       : {len(df_mem)}")
    print(f"  Provider nodes      : 50")
    print(f"  PatientDiagnosis    : {len(df_dx)}")
    print(f"  CareGap nodes       : {len(df_gaps)}")
    print(f"  RiskFactor nodes    : {len(df_rs)}")
    print(f"  SDOHBarrier nodes   : {len(df_sdoh)}")
    print(f"  Total edges         : {total}")


if __name__ == "__main__":
    main()
