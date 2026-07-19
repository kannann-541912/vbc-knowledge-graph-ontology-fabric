#!/usr/bin/env python3
"""
Load VBC ontology files into Neptune via direct SPARQL INSERT DATA.
Parses taxonomy.ttl, thesaurus.ttl, vbc_ontology.owl using rdflib,
then POSTs triples in batches to the Neptune SPARQL endpoint.

No IAM role or S3 bulk loader needed — works with network access only.

Usage: python3 load_ontology_neptune.py
"""
import os, sys
import requests, urllib3
from rdflib import Graph, URIRef, Literal, BNode
from rdflib.namespace import RDF, RDFS, OWL, SKOS, XSD

urllib3.disable_warnings()

NEPTUNE_HOST = "vbc-neptune-poc.cluster-cxe0k4i6swp1.ap-southeast-2.neptune.amazonaws.com"
NEPTUNE_PORT = 8182
SPARQL_URL   = f"https://{NEPTUNE_HOST}:{NEPTUNE_PORT}/sparql"
BATCH_SIZE   = 100

BASE     = os.path.join(os.path.dirname(__file__), "..", "..")
ONT_DIR  = os.path.join(BASE, "ontology")

FILES = [
    ("taxonomy.ttl",      "turtle", "https://ontology.vbc.internal/vbc/taxonomy"),
    ("thesaurus.ttl",     "turtle", "https://ontology.vbc.internal/vbc/thesaurus"),
    ("vbc_ontology.owl",  "xml",    "https://ontology.vbc.internal/vbc/ontology"),
]


def sparql_update(query: str, label: str = ""):
    resp = requests.post(
        SPARQL_URL,
        data={"update": query},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        verify=False, timeout=60,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"[{label}] HTTP {resp.status_code}: {resp.text[:400]}")


def term_to_sparql(term):
    """Convert an rdflib term to a SPARQL token string."""
    if isinstance(term, URIRef):
        return f"<{str(term)}>"
    elif isinstance(term, Literal):
        val = str(term).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        if term.datatype:
            return f'"{val}"^^<{str(term.datatype)}>'
        elif term.language:
            return f'"{val}"@{term.language}'
        else:
            return f'"{val}"'
    elif isinstance(term, BNode):
        return f"_:b{str(term)}"
    return None


def load_file(path: str, fmt: str, graph_uri: str):
    print(f"  Parsing {os.path.basename(path)}...")
    g = Graph()
    g.parse(path, format=fmt)
    triples_total = len(g)
    print(f"  Found {triples_total} triples — loading in batches of {BATCH_SIZE}...")

    batch, loaded, errors = [], 0, 0
    for s, p, o in g:
        st = term_to_sparql(s)
        pt = term_to_sparql(p)
        ot = term_to_sparql(o)
        if st and pt and ot:
            batch.append(f"{st} {pt} {ot} .")
        if len(batch) >= BATCH_SIZE:
            try:
                body = "\n    ".join(batch)
                sparql_update(
                    f"INSERT DATA {{ GRAPH <{graph_uri}> {{ {body} }} }}",
                    os.path.basename(path)
                )
                loaded += len(batch)
            except Exception as e:
                errors += 1
                print(f"    ⚠️  Batch error: {e}")
            batch = []

    # flush remainder
    if batch:
        try:
            body = "\n    ".join(batch)
            sparql_update(
                f"INSERT DATA {{ GRAPH <{graph_uri}> {{ {body} }} }}",
                os.path.basename(path)
            )
            loaded += len(batch)
        except Exception as e:
            errors += 1

    mark = "✅" if errors == 0 else "⚠️ "
    print(f"  {mark} {os.path.basename(path)}: {loaded}/{triples_total} triples loaded ({errors} batch errors)")
    return loaded, triples_total


def validate(graph_uri: str, expected_predicate: str, label: str):
    """Run a quick SPARQL SELECT to count triples with a specific predicate."""
    query = f"""
SELECT (COUNT(*) AS ?c)
WHERE {{
  GRAPH <{graph_uri}> {{
    ?s <{expected_predicate}> ?o .
  }}
}}"""
    resp = requests.post(
        SPARQL_URL.replace("/sparql", "/sparql"),
        data={"query": query},
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/sparql-results+json",
        },
        verify=False, timeout=30,
    )
    if resp.status_code == 200:
        count = int(resp.json()["results"]["bindings"][0]["c"]["value"])
        return count
    return -1


def main():
    print("=== Loading VBC ontology into Neptune ===\n")

    total_loaded = 0
    for fname, fmt, graph_uri in FILES:
        path = os.path.join(ONT_DIR, fname)
        if not os.path.exists(path):
            print(f"  ⚠️  File not found: {path}")
            continue
        loaded, total = load_file(path, fmt, graph_uri)
        total_loaded += loaded

    print(f"\n=== Validation ===")

    # subClassOf count (taxonomy)
    n = validate(
        "https://ontology.vbc.internal/vbc/taxonomy",
        "http://www.w3.org/2000/01/rdf-schema#subClassOf",
        "subClassOf triples"
    )
    mark = "✅" if n >= 130 else "❌"
    print(f"  {mark} taxonomy — rdfs:subClassOf triples: {n} (need ≥130)")

    # altLabel count (thesaurus)
    n2 = validate(
        "https://ontology.vbc.internal/vbc/thesaurus",
        "http://www.w3.org/2004/02/skos/core#altLabel",
        "skos:altLabel triples"
    )
    mark2 = "✅" if n2 >= 40 else "❌"
    print(f"  {mark2} thesaurus — skos:altLabel triples: {n2} (need ≥40)")

    print(f"\n  Total triples loaded this run: {total_loaded}")

    if n >= 130 and n2 >= 40:
        print("\n✅ Ontology load validation passed.")
    else:
        print("\n❌ Some validation gates failed — check above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
