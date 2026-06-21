"""
MañkeAnalytica — statistical analysis of press corpora.

mañke (condor, mapudungun) + analytica (analysis, latin)

Sources (in priority order): ÑarkiMundatio, FiluSententia, CulpemCorpus.
Filu files get extra analysis sections based on detected columns.
"""

import csv
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

from flask import Flask, jsonify, render_template, request

import yene_io

app = Flask(__name__)
HERE = Path(__file__).parent.resolve()

STOPWORDS_ES = {
    "de","la","el","en","y","a","los","las","del","se","que","un","una","con",
    "por","es","su","al","lo","mas","pero","o","este","esta","si","fue","ha",
    "para","son","como","no","le","sus","muy","entre","tambien","ser","ya","han",
    "hay","me","te","nos","les","era","ni","todo","todos","cuando","sobre","desde",
    "hasta","donde","tanto","sin","ante","tras","cada","estos","estas","aqui","asi",
    "bien","vez","dos","tres","anos","ano","dia","dias","parte","mismo","misma",
    "bajo","dentro","cual","cuales","quien","quienes","segun","durante","mediante",
    "hacia","contra","seran","sera","sido","esto","esos","esas","ese","esa",
    "nuestro","nuestra","veces","solo","all","more","than","with","the","for",
    "are","was","error",
}

LABEL_ORDER = ["muy_negativo", "negativo", "neutro", "positivo", "muy_positivo"]


# ── file discovery ────────────────────────────────────────────────────────────

def _candidate_dirs():
    """Return list of (dir, source_tag, glob_pattern) to search."""
    base   = HERE.parent
    parent = HERE.parent.parent
    return [
        (base   / "ÑarkiMundatio" / "output",  "narki",  "narki_*.csv"),
        (parent / "ÑarkiMundatio" / "output",  "narki",  "narki_*.csv"),
        (base   / "FiluSententia" / "output",  "filu",   "filu_*.csv"),
        (parent / "FiluSententia" / "output",  "filu",   "filu_*.csv"),
        (base   / "CulpemCorpus"  / "output",  "culpem", "culpem_*.csv"),
        (parent / "CulpemCorpus"  / "output",  "culpem", "culpem_*.csv"),
    ]


def read_meta(path: Path) -> dict:
    mp = path.with_suffix(".json")
    if mp.exists():
        with open(mp, encoding="utf-8") as f:
            return json.load(f)
    return {}


def all_files() -> list[dict]:
    seen  = set()
    files = []
    for dir_path, source, pattern in _candidate_dirs():
        if not dir_path.exists():
            continue
        for f in sorted(dir_path.glob(pattern),
                        key=lambda x: x.stat().st_mtime, reverse=True):
            if f.name in seen:
                continue
            seen.add(f.name)
            meta = read_meta(f)
            files.append({
                "name":   f.name,
                "path":   str(f),
                "size":   f.stat().st_size,
                "mtime":  f.stat().st_mtime,
                "source": source,
                "meta":   meta,
            })
    # Yene archives (SQLite) — read-only article sources for analysis
    for entry in yene_io.list_yene_sources():
        if entry["name"] not in seen:
            files.append(entry)
            seen.add(entry["name"])
    return files


def find_file(filename: str) -> Path | None:
    for dir_path, _, pattern in _candidate_dirs():
        fp = dir_path / filename
        if fp.exists():
            return fp
    return None


# ── routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/files")
def files():
    return jsonify({"files": all_files()})


@app.route("/analyse")
def analyse():
    filename = request.args.get("file", "").strip()
    top_n    = min(int(request.args.get("top_n", "40")), 200)

    if not filename or ".." in filename or "/" in filename or "\\" in filename:
        return jsonify({"error": "Invalid filename"}), 400

    if yene_io.is_yene_name(filename):
        try:
            rows = yene_io.load_yene_rows(filename)
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    else:
        filepath = find_file(filename)
        if not filepath:
            return jsonify({"error": "File not found"}), 404
        try:
            with open(filepath, encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    if not rows:
        return jsonify({"error": "Empty file"}), 400

    cols = set(rows[0].keys())

    # ── temporal ──────────────────────────────────────────────────────────────
    date_counts  = Counter()
    month_counts = Counter()
    year_counts  = Counter()
    for row in rows:
        d = (row.get("date") or row.get("fecha") or "")[:10]
        if re.match(r"\d{4}-\d{2}-\d{2}", d):
            date_counts[d]      += 1
            month_counts[d[:7]] += 1
            year_counts[d[:4]]  += 1

    # ── sources ───────────────────────────────────────────────────────────────
    source_counts = Counter(
        (row.get("source") or row.get("fuente") or "desconocido").strip()
        for row in rows
    )

    # ── lengths ───────────────────────────────────────────────────────────────
    lengths = [len((row.get("body_text") or "").split()) for row in rows]
    avg_len = round(sum(lengths) / len(lengths), 1) if lengths else 0
    buckets = {"0–50": 0, "51–150": 0, "151–300": 0, "301–500": 0, "500+": 0}
    for ln in lengths:
        if   ln <=  50: buckets["0–50"]    += 1
        elif ln <= 150: buckets["51–150"]  += 1
        elif ln <= 300: buckets["151–300"] += 1
        elif ln <= 500: buckets["301–500"] += 1
        else:           buckets["500+"]    += 1

    # ── word frequency ────────────────────────────────────────────────────────
    word_counter = Counter()
    for row in rows:
        body = (row.get("body_text") or "").lower()
        body = "".join(
            c for c in unicodedata.normalize("NFD", body)
            if unicodedata.category(c) != "Mn"
        )
        for w in re.findall(r"\b[a-z]{3,}\b", body):
            if w not in STOPWORDS_ES:
                word_counter[w] += 1

    result = {
        "n": len(rows),
        "temporal": {
            "by_date":  sorted(date_counts.items()),
            "by_month": sorted(month_counts.items()),
            "by_year":  sorted(year_counts.items()),
        },
        "sources": sorted(source_counts.items(), key=lambda x: -x[1]),
        "lengths": {
            "avg":     avg_len,
            "min":     min(lengths) if lengths else 0,
            "max":     max(lengths) if lengths else 0,
            "buckets": list(buckets.items()),
        },
        "top_words": word_counter.most_common(top_n),
        "filu":      {},
    }

    # ── filu: sentiment ───────────────────────────────────────────────────────
    has_sent = "body_sentiment_label" in cols or "title_sentiment_label" in cols
    if has_sent:
        sent = {}
        for prefix in ("body", "title"):
            lcol = f"{prefix}_sentiment_label"
            scol = f"{prefix}_sentiment_score"
            if lcol not in cols:
                continue
            label_dist = Counter(
                r.get(lcol, "").strip()
                for r in rows if r.get(lcol, "").strip()
                and r.get(lcol, "").strip() != "error"
            )
            # avg score by month
            score_by_month = defaultdict(list)
            for r in rows:
                d = (r.get("date") or "")[:7]
                s = r.get(scol, "")
                if d and s:
                    try:
                        score_by_month[d].append(float(s))
                    except ValueError:
                        pass
            avg_by_month = [
                (k, round(sum(v) / len(v), 2))
                for k, v in sorted(score_by_month.items())
            ]
            # avg score by source
            score_by_src = defaultdict(list)
            for r in rows:
                src = (r.get("source") or "desconocido").strip()
                s   = r.get(scol, "")
                if s:
                    try:
                        score_by_src[src].append(float(s))
                    except ValueError:
                        pass
            avg_by_src = sorted(
                [(k, round(sum(v)/len(v), 2)) for k, v in score_by_src.items()],
                key=lambda x: -x[1],
            )
            # label dist sorted by scale
            label_ordered = [(lbl, label_dist.get(lbl, 0)) for lbl in LABEL_ORDER
                             if label_dist.get(lbl, 0) > 0]
            sent[prefix] = {
                "label_dist":    label_ordered,
                "avg_by_month":  avg_by_month,
                "avg_by_source": avg_by_src,
            }
        result["filu"]["sentiment"] = sent

    # ── filu: content ─────────────────────────────────────────────────────────
    if "content_topic" in cols:
        topic_dist = Counter(
            r.get("content_topic", "").strip()
            for r in rows if r.get("content_topic", "").strip()
        )
        entity_counter = Counter()
        for r in rows:
            raw = r.get("content_entities", "") or ""
            for e in raw.split("|"):
                e = e.strip()
                if e and e != "error":
                    entity_counter[e] += 1
        result["filu"]["content"] = {
            "topics":   topic_dist.most_common(20),
            "entities": entity_counter.most_common(20),
        }

    # ── filu: questions ───────────────────────────────────────────────────────
    q_ids = sorted({
        m.group(1)
        for col in cols
        for m in [re.match(r"^(q\d+)_answer$", col)]
        if m
    }, key=lambda x: int(x[1:]))

    if q_ids:
        q_stats = {}
        for qid in q_ids:
            ans_col  = f"{qid}_answer"
            conf_col = f"{qid}_confidence"
            answers  = Counter(
                r.get(ans_col, "").strip().lower()
                for r in rows if r.get(ans_col, "").strip()
                and r.get(ans_col, "").strip() != "error"
            )
            conf = Counter(
                r.get(conf_col, "").strip().lower()
                for r in rows if r.get(conf_col, "").strip()
                and r.get(conf_col, "").strip() != "error"
            )
            q_stats[qid] = {"answers": dict(answers), "confidence": dict(conf)}

        # try to enrich with question text from saved question sets
        q_texts = {}
        qs_dirs = [
            HERE.parent / "FiluSententia" / "questions",
            HERE.parent.parent / "FiluSententia" / "questions",
        ]
        for qd in qs_dirs:
            if qd.exists():
                for qf in qd.glob("*.json"):
                    with open(qf, encoding="utf-8") as f:
                        qs = json.load(f)
                    for q in qs.get("questions", []):
                        q_texts[q["id"]] = q["text"]
                break

        result["filu"]["questions"] = {
            "ids":    q_ids,
            "stats":  q_stats,
            "texts":  q_texts,
        }

    return jsonify(result)


if __name__ == "__main__":
    app.run(debug=True, port=5005, threaded=True)
