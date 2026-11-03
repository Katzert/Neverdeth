#!/usr/bin/env python3
"""Exporta graphify-out/graph.json a un TXT legible para un agente (Argon).

Prioriza lo accionable: comunidades reales (sin la mega-comunidad del codegen),
inventario por archivo, aristas ENTRE archivos y hubs por grado. El arbol
generated/ (24,8k nodos) se resume en una linea para no inundar el archivo.

Salida: docs/GRAFO_ARGON.txt
"""
from __future__ import annotations

import collections
import json
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
GRAPH = PROJ / "graphify-out" / "graph.json"
OUT = PROJ / "docs" / "GRAFO_ARGON.txt"

g = json.loads(GRAPH.read_text(encoding="utf-8"))
nodes = g["nodes"]
links = g["links"]
by_id = {n["id"]: n for n in nodes}

# grado (entrada + salida)
deg = collections.Counter()
for e in links:
    deg[e["source"]] += 1
    deg[e["target"]] += 1

def has_file(n) -> bool:
    return bool(n.get("source_file"))

legible = [n for n in nodes if has_file(n)]
gen_nodes = [n for n in nodes if not has_file(n)]

# comunidades: tamaño, nombre, miembros legibles
comm = collections.defaultdict(list)
for n in nodes:
    comm[str(n.get("community", "?"))].append(n)

def comm_name(members):
    names = collections.Counter(m.get("community_name") or "" for m in members)
    raw = names.most_common(1)[0][0] or ""
    if raw and not raw.startswith(("DEFINE_REX_FUNC", "Community ")):
        return raw
    # cluster-only pierde los nombres curados: derivar de los 2 nodos con mas grado
    tops = sorted(members, key=lambda m: -deg[m["id"]])[:2]
    labels = [t.get("label", "?") for t in tops if t.get("label")]
    return " / ".join(labels)[:52] or "?"

rows = []
for cid, members in comm.items():
    rows.append((len(members), cid, comm_name(members), members))
rows.sort(reverse=True)

# aristas entre archivos distintos (mapa arquitectonico)
cross = collections.Counter()
for e in links:
    a, b = by_id.get(e["source"]), by_id.get(e["target"])
    if not (a and b and has_file(a) and has_file(b)):
        continue
    fa, fb = a["source_file"], b["source_file"]
    if fa == fb:
        continue
    cross[(fa, fb, e.get("relation", "?"))] += 1

# inventario por archivo
files = collections.defaultdict(list)
for n in legible:
    files[n["source_file"]].append(n)

L: list[str] = []
A = L.append

A("=" * 78)
A("GRAFO DEL PROYECTO — NeverDead PC Recompilation (ReXGlue)")
A("=" * 78)
A(f"Fuente      : graphify-out/graph.json")
A(f"Nodos       : {len(nodes):,}   Aristas: {len(links):,}   Comunidades: {len(comm)}")
A(f"Legibles    : {len(legible):,} nodos con archivo de origen "
  f"(src/ docs/ tools/ config/ + codegen)")
A(f"Codegen     : {len(gen_nodes):,} nodos SIN archivo (arbol generated/, una sola mega-comunidad)")
A("")
A("COMO LEERLO")
A("  1. RESUMEN .............. cifras y que contiene el grafo")
A("  2. COMUNIDADES .......... agrupaciones detectadas, con sus nodos principales")
A("  3. ARCHIVOS ............. inventario simbolo-por-archivo (lo accionable)")
A("  4. ARISTAS ENTRE ARCHIVOS mapa de dependencias reales entre ficheros")
A("  5. HUBS ................. nodos mas conectados (puntos de entrada al codigo)")
A("")
A("El arbol generated/ (24.8k nodos, todos sub_XXXXXXXX) se resume en 2.2: es")
A("codigo recompilado, no se edita a mano y solo interesa por sus simbolos puente.")
A("")

A("-" * 78)
A("1. RESUMEN")
A("-" * 78)
types = collections.Counter(n.get("file_type", "?") for n in nodes)
A("Tipos de nodo : " + ", ".join(f"{k}={v:,}" for k, v in types.most_common()))
origins = collections.Counter(n.get("_origin", "?") for n in nodes)
A("Procedencia   : " + ", ".join(f"{k}={v:,}" for k, v in origins.most_common()))
rel = collections.Counter(e.get("relation", "?") for e in links)
A("Relaciones    : " + ", ".join(f"{k}={v:,}" for k, v in rel.most_common(8)))
A("")
A("2.2  Comunidades del codegen (generated/neverdead_default)")
gen_only = [r for r in rows if not any(has_file(m) for m in r[3])]
gen_nodes_total = sum(r[0] for r in gen_only)
if gen_only:
    A(f"     {len(gen_only)} comunidades, {gen_nodes_total:,} nodos sin archivo de origen.")
    A("     No se listan: es codigo recompilado, no se edita a mano.")
else:
    A(f"     El arbol generated/ NO esta en este grafo: lo excluye .gitignore.")
    A(f"     Quedan {len(gen_nodes)} nodos sueltos sin archivo (tipos del SDK, imports).")
A("")

A("-" * 78)
A("2. COMUNIDADES CON CODIGO LEGIBLE (las unicas accionables)")
A("-" * 78)
mixed = [r for r in rows if any(has_file(m) for m in r[3]) and r[0] >= 2]
mixed.sort(key=lambda r: -len([m for m in r[3] if has_file(m)]))
rank = 0
for size, cid, name, members in mixed:
    if rank >= 40:
        A(f"  ... y {len(mixed) - 40} comunidades legibles mas")
        break
    rank += 1
    leg = [m for m in members if has_file(m)]
    gen_n = size - len(leg)
    tops = sorted(leg, key=lambda m: -deg[m["id"]])[:8]
    suffix = f" (+{gen_n} de codegen)" if gen_n else ""
    A(f"  [{cid:>3}] {name[:42]:<42} {len(leg):>4} legibles{suffix}")
    A("        " + ", ".join(f"{t['label']}({deg[t['id']]})" for t in tops))
A("")

A("-" * 78)
A("3. ARCHIVOS (simbolos por archivo de origen)")
A("-" * 78)
for f in sorted(files, key=lambda k: -len(files[k])):
    syms = sorted(files[f], key=lambda n: -deg[n["id"]])
    names = [s["label"] for s in syms[:14]]
    extra = "" if len(syms) <= 14 else f" … (+{len(syms) - 14})"
    A(f"  {f}  [{len(syms)}]")
    A("     " + ", ".join(names) + extra)
A("")

A("-" * 78)
A("4. ARISTAS ENTRE ARCHIVOS (dependencias reales, top 120)")
A("-" * 78)
for (fa, fb, r), c in cross.most_common(120):
    A(f"  {fa} -> {fb}   [{r}] x{c}")
if not cross:
    A("  (sin aristas entre archivos con origen: el grafo solo tiene AST intra-archivo)")
A("")

A("-" * 78)
A("5. HUBS (top 40 por grado, excluidos los nodos-macro tipo DEFINE_REX_FUNC())")
A("-" * 78)
import re as _re
macro = _re.compile(r"^[A-Z][A-Z0-9_]*\(\)$")
hubs = [n for n in nodes if not macro.match(n.get("label", "")) and n.get("label")]
for n in sorted(hubs, key=lambda x: -deg[x["id"]])[:40]:
    src = n.get("source_file") or "generated/"
    A(f"  {deg[n['id']]:>4}  {n['label'][:44]:<44} {src}")
A("")
A("=" * 78)
A("Fin. Regenerar con: python tools/export_graph_txt.py")
A("=" * 78)

OUT.write_text("\n".join(L), encoding="utf-8")
print(f"escrito {OUT} ({OUT.stat().st_size/1024:.0f} KB, {len(L)} lineas)")
print(f"legibles={len(legible)} archivos={len(files)} aristas_cruzadas={len(cross)}")
