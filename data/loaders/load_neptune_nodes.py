#!/usr/bin/env python3
"""
Load all VBC entity nodes into Neptune SPARQL endpoint.
Reads from local Parquet files, writes via SPARQL INSERT DATA in batches.

Prerequisites:
  - Neptune cluster AVAILABLE
  - taxonomy.ttl + thesaurus.ttl + vbc_ontology.owl already loaded via S3 bulk loader
  - Run load_neptune_edges.py after this script

Usage: python3 load_neptune_nodes.py
"""
import os, time
import pandas as pd
import requests, urllib3
urllib3.disable_warnings()

NEPTUNE_HOST = "vbc-neptune-poc.cluster-cxe0k4i6swp1.ap-southeast-2.neptune.amazonaws.com"
NEPTUNE_PORT = 8182
SPARQL_URL   = f"https://{NEPTUNE_HOST}:{NEPTUNE_PORT}/sparql"
VBC_NS       = "https://ontology.vbc.internal/vbc#"
GRAPH        = "https://ontology.vbc.internal/vbc/instances"
BATCH_SIZE   = 50

BASE   = os.path.join(os.path.dirname(__file__), "..", "..")
SAMPLE = os.path.join(BASE, "data", "sample")


def sparql_update(query: str, label: str = ""):
    resp = requests.post(
        SPARQL_URL,
        data={"update": query},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        verify=False, timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"SPARQL [{label}] HTTP {resp.status_code}: {resp.text[:300]}")


def esc(val):
    if val is None:
        return None
    s = str(val)
    if s in ("nan", "NaT", "None", ""):
        return None
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def build_insert(triples):
    body = "\n    ".join(triples)
    return f"""PREFIX vbc: <{VBC_NS}>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
INSERT DATA {{
  GRAPH <{GRAPH}> {{
    {body}
  }}
}}"""


def load_in_batches(rows, build_fn, label):
    triples, loaded, errors = [], 0, 0
    for row in rows:
        t = build_fn(row)
        if t:
            triples.extend(t)
        if len(triples) >= BATCH_SIZE:
            try:
                sparql_update(build_insert(triples), label)
                loaded += len(triples)
            except Exception as e:
                errors += 1
                print(f"  ⚠️  Batch error: {e}")
            triples = []
    if triples:
        try:
            sparql_update(build_insert(triples), label)
            loaded += len(triples)
        except Exception as e:
            errors += 1
    mark = "✅" if errors == 0 else "⚠️ "
    print(f"  {mark} {label}: {loaded} triples ({errors} errors)")
    return loaded


def close(lst):
    if lst:
        lst[-1] = lst[-1].rstrip(" ;") + " ."
    return lst


# ── Node builders ─────────────────────────────────────────────────────────────

def patient_triples(row):
    mid = esc(getattr(row, "member_id", None))
    if not mid: return None
    t = [f'vbc:Patient_{mid} a vbc:Patient ;']
    if esc(getattr(row, "mrn", None)):
        t.append(f'    vbc:mrn "{esc(row.mrn)}" ;')
    if esc(getattr(row, "dob", None)):
        t.append(f'    vbc:dateOfBirth "{str(row.dob)[:10]}"^^xsd:date ;')
    if esc(getattr(row, "gender", None)):
        t.append(f'    vbc:sex "{esc(row.gender)}" ;')
    if esc(getattr(row, "preferred_language", None)):
        t.append(f'    vbc:preferredLanguage "{esc(row.preferred_language)}" ;')
    if esc(getattr(row, "risk_tier", None)):
        t.append(f'    vbc:riskTier "{esc(row.risk_tier)}" ;')
    if getattr(row, "adi_score", None) is not None:
        t.append(f'    vbc:adiScore {int(row.adi_score)} ;')
    if esc(getattr(row, "pcp_npi", None)):
        t.append(f'    vbc:assignedPCPNPI "{esc(row.pcp_npi)}" ;')
    return close(t)


def provider_triples(row):
    npi = esc(getattr(row, "npi", None))
    if not npi: return None
    pt = getattr(row, "provider_type", "Specialist")
    cls = "PrimaryCarePhysician" if pt == "PCP" else "Specialist"
    t = [f'vbc:{cls}_{npi} a vbc:{cls} ;']
    t.append(f'    vbc:npi "{npi}" ;')
    fn = esc(getattr(row, "first_name", None))
    ln = esc(getattr(row, "last_name", None))
    if fn and ln:
        t.append(f'    vbc:hasFullName "{fn} {ln}" ;')
    if esc(getattr(row, "specialty", None)):
        t.append(f'    vbc:specialty "{esc(row.specialty)}" ;')
    if esc(getattr(row, "network_id", None)):
        t.append(f'    vbc:networkId "{esc(row.network_id)}" ;')
    if esc(getattr(row, "network_name", None)):
        t.append(f'    vbc:networkName "{esc(row.network_name)}" ;')
    return close(t)


def diagnosis_triples(row):
    did = esc(getattr(row, "diagnosis_id", None))
    if not did: return None
    t = [f'vbc:PatientDiagnosis_{did} a vbc:PatientDiagnosis ;']
    if esc(getattr(row, "icd10_cm_code", None)):
        t.append(f'    vbc:icd10CodeValue "{esc(row.icd10_cm_code)}" ;')
    if esc(getattr(row, "icd10_description", None)):
        t.append(f'    vbc:icd10Description "{esc(row.icd10_description)}" ;')
    if esc(getattr(row, "diagnosis_date", None)):
        t.append(f'    vbc:diagnosisDate "{str(row.diagnosis_date)[:10]}"^^xsd:date ;')
    if esc(getattr(row, "diagnosis_type", None)):
        t.append(f'    vbc:diagnosisType "{esc(row.diagnosis_type)}" ;')
    hcc = esc(getattr(row, "hcc_code", None))
    if hcc and hcc != "HCC0":
        t.append(f'    vbc:hccCode "{hcc}" ;')
        t.append(f'    vbc:rafWeight "{row.raf_weight}"^^xsd:decimal ;')
    return close(t)


def caregap_triples(row):
    gid = esc(getattr(row, "gap_id", None))
    if not gid: return None
    status = esc(getattr(row, "gap_status", "open")) or "open"
    cls = {"open": "OpenCareGap", "closed": "ClosedCareGap", "excluded": "ExcludedCareGap"}.get(status, "CareGap")
    t = [f'vbc:CareGap_{gid} a vbc:{cls} ;']
    if esc(getattr(row, "measure_id", None)):
        t.append(f'    vbc:measureId "{esc(row.measure_id)}" ;')
    if esc(getattr(row, "measure_name", None)):
        t.append(f'    vbc:measureName "{esc(row.measure_name)}" ;')
    t.append(f'    vbc:gapStatus "{status}" ;')
    if esc(getattr(row, "open_date", None)):
        t.append(f'    vbc:gapOpenDate "{str(row.open_date)[:10]}"^^xsd:date ;')
    close_dt = esc(getattr(row, "close_date", None))
    if close_dt and close_dt not in ("None", "nan", "NaT"):
        t.append(f'    vbc:gapCloseDate "{str(row.close_date)[:10]}"^^xsd:date ;')
    return close(t)


def riskfactor_triples(row):
    sid = esc(getattr(row, "score_id", None))
    if not sid: return None
    t = [f'vbc:RiskFactor_{sid} a vbc:RiskFactor ;']
    t.append(f'    vbc:riskScoreValue "{row.risk_score_value}"^^xsd:decimal ;')
    t.append(f'    vbc:riskTier "{esc(row.risk_tier)}" ;')
    t.append(f'    vbc:rafScore "{row.raf_score}"^^xsd:decimal ;')
    if getattr(row, "hcc_count", None) is not None:
        t.append(f'    vbc:hccCount {int(row.hcc_count)} ;')
    if esc(getattr(row, "top_hcc_code", None)):
        t.append(f'    vbc:topHccCode "{esc(row.top_hcc_code)}" ;')
    return close(t)


def sdoh_triples(row):
    bid = esc(getattr(row, "barrier_id", None))
    if not bid: return None
    t = [f'vbc:SDOHBarrier_{bid} a vbc:SDOHBarrier ;']
    if esc(getattr(row, "barrier_type", None)):
        t.append(f'    vbc:barrierType "{esc(row.barrier_type)}" ;')
    if esc(getattr(row, "barrier_category", None)):
        t.append(f'    vbc:barrierCategory "{esc(row.barrier_category)}" ;')
    if esc(getattr(row, "icd10_z_code", None)):
        t.append(f'    vbc:icd10ZCode "{esc(row.icd10_z_code)}" ;')
    if esc(getattr(row, "severity", None)):
        t.append(f'    vbc:severity "{esc(row.severity)}" ;')
    if getattr(row, "adi_score", None) is not None:
        t.append(f'    vbc:adiScore {int(row.adi_score)} ;')
    return close(t)


def main():
    print("=== Loading Neptune nodes ===\n")
    loaders = [
        ("members.parquet",       patient_triples,    "Patient nodes"),
        ("providers.parquet",     provider_triples,   "Provider nodes"),
        ("diagnoses.parquet",     diagnosis_triples,  "PatientDiagnosis nodes"),
        ("hedis_gaps.parquet",    caregap_triples,    "CareGap nodes"),
        ("risk_scores.parquet",   riskfactor_triples, "RiskFactor nodes"),
        ("sdoh_barriers.parquet", sdoh_triples,       "SDOHBarrier nodes"),
    ]
    total = 0
    for fname, fn, label in loaders:
        df = pd.read_parquet(os.path.join(SAMPLE, fname))
        print(f"  Loading {label} ({len(df)} rows)...")
        total += load_in_batches(df.itertuples(index=False, name="Row"), fn, label)
    print(f"\n✅ Total node triples loaded: {total}")


if __name__ == "__main__":
    main()
