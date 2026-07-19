#!/usr/bin/env python3
"""
Load VBC ontology + knowledge graph into Neptune via the vbc-sparql-relay Lambda.
The Lambda runs inside the VPC and forwards SPARQL to Neptune's private endpoint.

Stages:
  1. Load taxonomy.ttl, thesaurus.ttl, vbc_ontology.owl (schema)
  2. Load all node types (Patient, Provider, Diagnosis, CareGap, RiskFactor, SDOH)
  3. Load all edge types (hasDiagnosis, hasCareGap, hasRiskScore, hasSDOHBarrier, hasPCP)

Usage: python3 load_neptune_via_lambda.py
"""
import boto3, json, os, sys
import pandas as pd
from rdflib import Graph, URIRef, Literal, BNode

REGION       = "ap-southeast-2"
LAMBDA_NAME  = "vbc-sparql-relay"
VBC_NS       = "https://ontology.vbc.internal/vbc#"
GRAPH_SCHEMA = "https://ontology.vbc.internal/vbc/schema"
GRAPH_INST   = "https://ontology.vbc.internal/vbc/instances"
BATCH_SIZE   = 60

BASE    = os.path.join(os.path.dirname(__file__), "..", "..")
ONT_DIR = os.path.join(BASE, "ontology")
SAMPLE  = os.path.join(BASE, "data", "sample")

lam = boto3.client("lambda", region_name=REGION)


def invoke_relay(sparql: str, qtype: str = "update") -> dict:
    resp = lam.invoke(
        FunctionName=LAMBDA_NAME,
        InvocationType="RequestResponse",
        Payload=json.dumps({"type": qtype, "sparql": sparql}).encode(),
    )
    result = json.loads(resp["Payload"].read())
    if result.get("statusCode", 200) not in (200, 201):
        raise RuntimeError(f"Neptune error: {result.get('body','')[:300]}")
    return result


def build_insert(triples: list, graph: str) -> str:
    body = "\n    ".join(triples)
    return f"INSERT DATA {{ GRAPH <{graph}> {{ {body} }} }}"


def flush_batch(batch: list, graph: str, label: str) -> int:
    if not batch:
        return 0
    try:
        invoke_relay(build_insert(batch, graph))
        return len(batch)
    except Exception as e:
        print(f"    ⚠️  Batch error [{label}]: {e}")
        return 0


def load_rdf_file(path: str, fmt: str, graph: str, label: str):
    print(f"  Parsing {os.path.basename(path)}...")
    g = Graph()
    g.parse(path, format=fmt)
    print(f"  {len(g)} triples — loading via Lambda...")

    def term(t):
        if isinstance(t, URIRef):   return f"<{t}>"
        if isinstance(t, BNode):    return f"_:b{t}"
        if isinstance(t, Literal):
            v = str(t).replace("\\","\\\\").replace('"','\\"').replace("\n"," ")
            if t.datatype:  return f'"{v}"^^<{t.datatype}>'
            if t.language:  return f'"{v}"@{t.language}'
            return f'"{v}"'
        return None

    batch, loaded, errors = [], 0, 0
    for s, p, o in g:
        st, pt, ot = term(s), term(p), term(o)
        if st and pt and ot:
            batch.append(f"{st} {pt} {ot} .")
        if len(batch) >= BATCH_SIZE:
            n = flush_batch(batch, graph, label)
            loaded += n
            if n < len(batch): errors += 1
            batch = []
    if batch:
        n = flush_batch(batch, graph, label)
        loaded += n

    mark = "✅" if errors == 0 else "⚠️ "
    print(f"  {mark} {label}: {loaded}/{len(g)} triples loaded")
    return loaded


def esc(v):
    if v is None: return None
    s = str(v)
    return None if s in ("nan","NaT","None","") else s.replace("\\","\\\\").replace('"','\\"').replace("\n"," ")


def close(lst):
    if lst: lst[-1] = lst[-1].rstrip(" ;") + " ."
    return lst


# ── Node builders ─────────────────────────────────────────────────────────────
def patient_triples(r):
    mid = esc(getattr(r,"member_id",None))
    if not mid: return None
    t = [f'<{VBC_NS}Patient_{mid}> <{VBC_NS}type> <{VBC_NS}Patient> ;']
    t.append(f'    <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <{VBC_NS}Patient> ;')
    if esc(getattr(r,"mrn",None)):
        t.append(f'    <{VBC_NS}mrn> "{esc(r.mrn)}" ;')
    if esc(getattr(r,"dob",None)):
        t.append(f'    <{VBC_NS}dateOfBirth> "{str(r.dob)[:10]}"^^<http://www.w3.org/2001/XMLSchema#date> ;')
    if esc(getattr(r,"gender",None)):
        t.append(f'    <{VBC_NS}sex> "{esc(r.gender)}" ;')
    if esc(getattr(r,"risk_tier",None)):
        t.append(f'    <{VBC_NS}riskTier> "{esc(r.risk_tier)}" ;')
    if getattr(r,"adi_score",None) is not None:
        t.append(f'    <{VBC_NS}adiScore> {int(r.adi_score)} ;')
    if esc(getattr(r,"pcp_npi",None)):
        t.append(f'    <{VBC_NS}assignedPCPNPI> "{esc(r.pcp_npi)}" ;')
    return close(t)


def provider_triples(r):
    npi = esc(getattr(r,"npi",None))
    if not npi: return None
    pt  = getattr(r,"provider_type","Specialist")
    cls = "PrimaryCarePhysician" if pt == "PCP" else "Specialist"
    t   = [f'<{VBC_NS}{cls}_{npi}> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <{VBC_NS}{cls}> ;']
    t.append(f'    <{VBC_NS}npi> "{npi}" ;')
    fn = esc(getattr(r,"first_name",None)); ln = esc(getattr(r,"last_name",None))
    if fn and ln:
        t.append(f'    <{VBC_NS}hasFullName> "{fn} {ln}" ;')
    if esc(getattr(r,"specialty",None)):
        t.append(f'    <{VBC_NS}specialty> "{esc(r.specialty)}" ;')
    if esc(getattr(r,"network_id",None)):
        t.append(f'    <{VBC_NS}networkId> "{esc(r.network_id)}" ;')
    if esc(getattr(r,"network_name",None)):
        t.append(f'    <{VBC_NS}networkName> "{esc(r.network_name)}" ;')
    return close(t)


def diagnosis_triples(r):
    did = esc(getattr(r,"diagnosis_id",None))
    if not did: return None
    t = [f'<{VBC_NS}PatientDiagnosis_{did}> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <{VBC_NS}PatientDiagnosis> ;']
    if esc(getattr(r,"icd10_cm_code",None)):
        t.append(f'    <{VBC_NS}icd10CodeValue> "{esc(r.icd10_cm_code)}" ;')
    if esc(getattr(r,"icd10_description",None)):
        t.append(f'    <{VBC_NS}icd10Description> "{esc(r.icd10_description)}" ;')
    if esc(getattr(r,"diagnosis_date",None)):
        t.append(f'    <{VBC_NS}diagnosisDate> "{str(r.diagnosis_date)[:10]}"^^<http://www.w3.org/2001/XMLSchema#date> ;')
    if esc(getattr(r,"diagnosis_type",None)):
        t.append(f'    <{VBC_NS}diagnosisType> "{esc(r.diagnosis_type)}" ;')
    hcc = esc(getattr(r,"hcc_code",None))
    if hcc and hcc != "HCC0":
        t.append(f'    <{VBC_NS}hccCode> "{hcc}" ;')
        t.append(f'    <{VBC_NS}rafWeight> "{r.raf_weight}"^^<http://www.w3.org/2001/XMLSchema#decimal> ;')
    return close(t)


def caregap_triples(r):
    gid    = esc(getattr(r,"gap_id",None))
    if not gid: return None
    status = esc(getattr(r,"gap_status","open")) or "open"
    cls    = {"open":"OpenCareGap","closed":"ClosedCareGap","excluded":"ExcludedCareGap"}.get(status,"CareGap")
    t = [f'<{VBC_NS}CareGap_{gid}> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <{VBC_NS}{cls}> ;']
    if esc(getattr(r,"measure_id",None)):
        t.append(f'    <{VBC_NS}measureId> "{esc(r.measure_id)}" ;')
    if esc(getattr(r,"measure_name",None)):
        t.append(f'    <{VBC_NS}measureName> "{esc(r.measure_name)}" ;')
    t.append(f'    <{VBC_NS}gapStatus> "{status}" ;')
    if esc(getattr(r,"open_date",None)):
        t.append(f'    <{VBC_NS}gapOpenDate> "{str(r.open_date)[:10]}"^^<http://www.w3.org/2001/XMLSchema#date> ;')
    cd = esc(getattr(r,"close_date",None))
    if cd and cd not in ("None","nan","NaT"):
        t.append(f'    <{VBC_NS}gapCloseDate> "{str(r.close_date)[:10]}"^^<http://www.w3.org/2001/XMLSchema#date> ;')
    return close(t)


def riskfactor_triples(r):
    sid = esc(getattr(r,"score_id",None))
    if not sid: return None
    t = [f'<{VBC_NS}RiskFactor_{sid}> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <{VBC_NS}RiskFactor> ;']
    t.append(f'    <{VBC_NS}riskScoreValue> "{r.risk_score_value}"^^<http://www.w3.org/2001/XMLSchema#decimal> ;')
    t.append(f'    <{VBC_NS}riskTier> "{esc(r.risk_tier)}" ;')
    t.append(f'    <{VBC_NS}rafScore> "{r.raf_score}"^^<http://www.w3.org/2001/XMLSchema#decimal> ;')
    if getattr(r,"hcc_count",None) is not None:
        t.append(f'    <{VBC_NS}hccCount> {int(r.hcc_count)} ;')
    if esc(getattr(r,"top_hcc_code",None)):
        t.append(f'    <{VBC_NS}topHccCode> "{esc(r.top_hcc_code)}" ;')
    return close(t)


def sdoh_triples(r):
    bid = esc(getattr(r,"barrier_id",None))
    if not bid: return None
    t = [f'<{VBC_NS}SDOHBarrier_{bid}> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <{VBC_NS}SDOHBarrier> ;']
    if esc(getattr(r,"barrier_type",None)):
        t.append(f'    <{VBC_NS}barrierType> "{esc(r.barrier_type)}" ;')
    if esc(getattr(r,"barrier_category",None)):
        t.append(f'    <{VBC_NS}barrierCategory> "{esc(r.barrier_category)}" ;')
    if esc(getattr(r,"icd10_z_code",None)):
        t.append(f'    <{VBC_NS}icd10ZCode> "{esc(r.icd10_z_code)}" ;')
    if esc(getattr(r,"severity",None)):
        t.append(f'    <{VBC_NS}severity> "{esc(r.severity)}" ;')
    if getattr(r,"adi_score",None) is not None:
        t.append(f'    <{VBC_NS}adiScore> {int(r.adi_score)} ;')
    return close(t)


def load_nodes(fname, build_fn, label):
    df     = pd.read_parquet(os.path.join(SAMPLE, fname))
    batch, loaded, errors = [], 0, 0
    for r in df.itertuples(index=False, name="R"):
        t = build_fn(r)
        if t: batch.extend(t)
        if len(batch) >= BATCH_SIZE:
            n = flush_batch(batch, GRAPH_INST, label)
            loaded += n; batch = []
            if n == 0: errors += 1
    if batch:
        n = flush_batch(batch, GRAPH_INST, label)
        loaded += n
    mark = "✅" if errors == 0 else "⚠️ "
    print(f"  {mark} {label}: {loaded} triples from {len(df)} rows")
    return loaded


def load_edges():
    total = 0
    edge_sets = [
        ("diagnoses.parquet",     "member_id", "PatientDiagnosis",
         "diagnosis_id", "hasDiagnosis", "Patient_{s}", "PatientDiagnosis_{o}"),
        ("hedis_gaps.parquet",    "member_id", "CareGap",
         "gap_id",       "hasCareGap",   "Patient_{s}", "CareGap_{o}"),
        ("risk_scores.parquet",   "member_id", "RiskFactor",
         "score_id",     "hasRiskScore", "Patient_{s}", "RiskFactor_{o}"),
        ("sdoh_barriers.parquet", "member_id", "SDOHBarrier",
         "barrier_id",   "hasSDOHBarrier","Patient_{s}", "SDOHBarrier_{o}"),
    ]
    for fname, scol, _, ocol, prop, sfmt, ofmt in edge_sets:
        df    = pd.read_parquet(os.path.join(SAMPLE, fname))
        batch = []
        loaded = 0
        for r in df.itertuples(index=False, name="R"):
            s = esc(getattr(r, scol, None))
            o = esc(getattr(r, ocol, None))
            if s and o:
                snode = sfmt.replace("{s}", s)
                onode = ofmt.replace("{o}", o)
                batch.append(f"<{VBC_NS}{snode}> <{VBC_NS}{prop}> <{VBC_NS}{onode}> .")
            if len(batch) >= BATCH_SIZE:
                loaded += flush_batch(batch, GRAPH_INST, prop)
                batch = []
        if batch:
            loaded += flush_batch(batch, GRAPH_INST, prop)
        print(f"  ✅ {prop}: {loaded} edges")
        total += loaded

    # hasPCP edges from members
    df_m  = pd.read_parquet(os.path.join(SAMPLE, "members.parquet"))
    batch = []
    loaded = 0
    for r in df_m.itertuples(index=False, name="R"):
        mid = esc(getattr(r,"member_id",None))
        npi = esc(getattr(r,"pcp_npi",None))
        if mid and npi:
            batch.append(f"<{VBC_NS}Patient_{mid}> <{VBC_NS}hasPCP> <{VBC_NS}PrimaryCarePhysician_{npi}> .")
        if len(batch) >= BATCH_SIZE:
            loaded += flush_batch(batch, GRAPH_INST, "hasPCP")
            batch = []
    if batch:
        loaded += flush_batch(batch, GRAPH_INST, "hasPCP")
    print(f"  ✅ hasPCP: {loaded} edges")
    total += loaded
    return total


def sparql_count(graph: str, predicate: str) -> int:
    q = f"SELECT (COUNT(*) AS ?c) WHERE {{ GRAPH <{graph}> {{ ?s <{predicate}> ?o . }} }}"
    try:
        r = invoke_relay(q, "query")
        data = json.loads(r["body"])
        return int(data["results"]["bindings"][0]["c"]["value"])
    except Exception as e:
        print(f"    ⚠️  Count query failed: {e}")
        return -1


def main():
    print("=== Stage 1: Load ontology schema into Neptune ===\n")
    for fname, fmt, graph, label in [
        ("taxonomy.ttl",     "turtle", GRAPH_SCHEMA, "Taxonomy"),
        ("thesaurus.ttl",    "turtle", GRAPH_SCHEMA, "Thesaurus"),
        ("vbc_ontology.owl", "xml",    GRAPH_SCHEMA, "OWL Ontology"),
    ]:
        load_rdf_file(os.path.join(ONT_DIR, fname), fmt, graph, label)

    print("\n=== Stage 2: Load instance nodes ===\n")
    loaders = [
        ("members.parquet",       patient_triples,    "Patient nodes"),
        ("providers.parquet",     provider_triples,   "Provider nodes"),
        ("diagnoses.parquet",     diagnosis_triples,  "PatientDiagnosis nodes"),
        ("hedis_gaps.parquet",    caregap_triples,    "CareGap nodes"),
        ("risk_scores.parquet",   riskfactor_triples, "RiskFactor nodes"),
        ("sdoh_barriers.parquet", sdoh_triples,       "SDOHBarrier nodes"),
    ]
    node_total = sum(load_nodes(f, fn, lbl) for f, fn, lbl in loaders)

    print("\n=== Stage 3: Load edges ===\n")
    edge_total = load_edges()

    print("\n=== Validation ===")
    sc = sparql_count(GRAPH_SCHEMA, "http://www.w3.org/2000/01/rdf-schema#subClassOf")
    al = sparql_count(GRAPH_SCHEMA, "http://www.w3.org/2004/02/skos/core#altLabel")
    pt = sparql_count(GRAPH_INST,   f"{VBC_NS}riskTier")
    rt = sparql_count(GRAPH_INST,   f"{VBC_NS}riskScoreValue")
    hd = sparql_count(GRAPH_INST,   f"{VBC_NS}hasDiagnosis")

    print(f"  {'✅' if sc >= 48 else '❌'} rdfs:subClassOf triples : {sc} (need ≥48)")
    print(f"  {'✅' if al >= 40 else '❌'} skos:altLabel triples   : {al} (need ≥40)")
    print(f"  {'✅' if pt >= 450 else '❌'} Patient nodes           : {pt} (need ≥450)")
    print(f"  {'✅' if rt >= 450 else '❌'} RiskFactor nodes        : {rt} (need ≥450)")
    print(f"  {'✅' if hd >= 2500 else '❌'} hasDiagnosis edges      : {hd} (need ≥2500)")

    print(f"\n  Node triples: {node_total} | Edge triples: {edge_total}")
    print(f"  Total loaded: {node_total + edge_total}")

    if sc >= 48 and al >= 40 and pt >= 450 and hd >= 2500:
        print("\n✅ Phase 3 validation gates passed.")
    else:
        print("\n❌ Some gates failed — check above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
