"""E1~E3 + M1~M14 실행. `results/` 에 JSON 과 summary.md 를 쓴다. 결과 파일은 손으로 고치지 않는다.

순서: 범위 가드 → 파생 파일 해시 확인 → oracle(별도 프로세스, bia 미로딩 확인) → 엔진 → A1~A3 →
예측 P1~P7 → 손상 M1~M14(A4) → 요약.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
RESULTS = os.path.join(HERE, "results")
DERIVED = os.path.join(HERE, "derived")
FLOAT_TOL = 1e-9
GUARDED = ("bia", "data", "eval", "tests", "scripts")


def _git(*args):
    return subprocess.run(("git",) + args, cwd=REPO, capture_output=True, text=True,
                          check=True).stdout


def scope_guard():
    diff = _git("diff", "main", "--stat", "--", *GUARDED)
    status = _git("status", "--porcelain", "--", *GUARDED)
    return {"command": "git diff main -- %s" % " ".join(GUARDED),
            "diff_empty": diff.strip() == "", "untracked_or_modified_empty": status.strip() == "",
            "head": _git("rev-parse", "HEAD").strip()}


guard = scope_guard()
if not (guard["diff_empty"] and guard["untracked_or_modified_empty"]):
    print("STOP: guarded paths differ from main: %s" % guard)
    sys.exit(10)

sys.path.insert(0, HERE)
import mutations as M  # noqa: E402
import spec as S  # noqa: E402
from loader import LoaderRejected, derive, read_allowlisted  # noqa: E402

from bia.analysis.compiler import compile_request  # noqa: E402
from bia.analysis.engine import run_plan  # noqa: E402
from bia.analysis.errors import AnalysisRefused, RequestError, SpecError  # noqa: E402
from bia.analysis.frame import load_observations  # noqa: E402
from bia.analysis.request import AnalysisRequest, PeriodComparison  # noqa: E402
from bia.types import Period  # noqa: E402

REFUSALS = (SpecError, RequestError, AnalysisRefused, LoaderRejected)


def write_json(name, obj):
    os.makedirs(RESULTS, exist_ok=True)
    with open(os.path.join(RESULTS, name), "w", encoding="utf-8") as handle:
        json.dump(obj, handle, indent=1, ensure_ascii=False, sort_keys=True)
        handle.write("\n")


def sha256_file(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


# ------------------------------------------------------------------ 엔진


def engine_run(path, experiment, metric_name):
    domain = S.DOMAINS[experiment["domain"]]
    metric = domain.metrics[metric_name]
    request = AnalysisRequest(
        domain=domain.name, metric=metric_name, breakdowns=tuple(experiment["breakdowns"]),
        comparison=PeriodComparison(current=Period.of(*experiment["current"]),
                                    baseline=Period.of(*experiment["baseline"])))
    plan = compile_request(request)
    rows = load_observations(path, domain, metric.columns)
    return run_plan(plan, rows).as_dict()


def guarded_run(fn, *args):
    try:
        return {"result": fn(*args), "error": None}
    except Exception as exc:  # noqa: BLE001 — 분류 규칙이 예외 종류를 본다
        return {"result": None, "error": type(exc).__name__,
                "error_is_refusal": isinstance(exc, REFUSALS),
                "stage": getattr(exc, "stage", None), "message": str(exc)[:400]}


# ------------------------------------------------------------------ oracle


def run_oracle():
    code = ("import sys, json; sys.path.insert(0, %r); sys.path.insert(1, %r); import oracle; "
            "r = oracle.main_stdin(); "
            "mods = sorted(m for m in sys.modules if m == 'bia' or m.startswith('bia.')); "
            "json.dump({'bia_modules_loaded': mods, 'bia_importable': True, 'results': r}, sys.stdout)"
            % (HERE, REPO))
    payload = json.dumps({"derived_dir": DERIVED, "experiments": list(S.EXPERIMENTS)})
    proc = subprocess.run([sys.executable, "-I", "-c", code], input=payload,
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("oracle failed: %s" % proc.stderr[-2000:])
    return json.loads(proc.stdout)


# ------------------------------------------------------------------ A1~A3


class Tally:
    def __init__(self):
        self.ints = 0
        self.floats = 0
        self.flags = 0
        self.flags_ambiguous = 0
        self.disputed_band = 0
        self.max_float_diff = 0.0
        self.mismatches = []

    def int_eq(self, where, a, b):
        self.ints += 1
        if not (isinstance(a, int) and isinstance(b, int) and a == b):
            self.mismatches.append({"where": where, "engine": a, "oracle": b, "kind": "int"})

    def float_eq(self, where, a, b):
        self.floats += 1
        if a is None or b is None:
            if a is not b:
                self.mismatches.append({"where": where, "engine": a, "oracle": b, "kind": "float"})
            return
        diff = abs(float(a) - float(b))
        self.max_float_diff = max(self.max_float_diff, diff)
        if diff > FLOAT_TOL:
            self.mismatches.append({"where": where, "engine": a, "oracle": b, "kind": "float",
                                    "diff": diff})

    def other(self, where, a, b, kind):
        if a != b:
            self.mismatches.append({"where": where, "engine": a, "oracle": b, "kind": kind})


def compare(label, engine, oracle, arith, flag_t, cross_rows):
    if engine.get("error") or "refused" in oracle:
        arith.other(label + ":refusal", engine.get("stage") if engine.get("error") else None,
                    oracle.get("refused"), "refusal")
        return
    res = engine["result"]
    kind = res["metric"]["kind"]
    for field in ("current", "baseline", "delta"):
        if kind == "additive":
            arith.int_eq("%s:comparison.%s" % (label, field), res["comparison"][field],
                         oracle["comparison"][field])
        else:
            arith.float_eq("%s:comparison.%s" % (label, field), res["comparison"][field],
                           oracle["comparison"][field])
    arith.float_eq(label + ":comparison.relative_change", res["comparison"]["relative_change"],
                   oracle["comparison"]["relative_change"])
    for eb, ob in zip(res["breakdowns"], oracle["breakdowns"]):
        where = "%s:%s" % (label, "x".join(eb["dimensions"]) or "overall")
        arith.other(where + ":dimensions", eb["dimensions"], ob["dimensions"], "shape")
        if eb["cross"]:
            rule = "omitted" if ob["observed_cells"] > 1000 else "ok"
            row = {"where": where, "engine_status": eb["status"], "oracle_status": ob["status"],
                   "oracle_rule_status": rule, "oracle_observed_cells": ob["observed_cells"],
                   "engine_observed_cells": eb.get("observed_cells"),
                   "engine_groups": len(eb.get("groups", [])) if eb["status"] == "ok" else None}
            row["match"] = (eb["status"] == rule == ob["status"] and (
                eb.get("observed_cells") == ob["observed_cells"] if eb["status"] == "omitted"
                else len(eb["groups"]) == ob["observed_cells"]))
            cross_rows.append(row)
        if eb["status"] != ob["status"]:
            arith.other(where + ":status", eb["status"], ob["status"], "status")
            continue
        if eb["status"] != "ok":
            continue
        eg = {tuple(g["key"][d] for d in eb["dimensions"]): g for g in eb["groups"]}
        og = {tuple(g["key"]): g for g in ob["groups"]}
        arith.other(where + ":group_keys", len(eg), len(og), "group_count")
        if set(eg) != set(og):
            arith.other(where + ":group_key_set", sorted(set(eg) - set(og))[:5],
                        sorted(set(og) - set(eg))[:5], "group_keys")
        for key in sorted(set(eg) & set(og)):
            e, o = eg[key], og[key]
            gw = "%s[%s]" % (where, "|".join(key))
            arith.other(gw + ".comparable", e["comparable"], o["comparable"], "bool")
            arith.float_eq(gw + ".net_contribution", e["net_contribution"], o["net_contribution"])
            if kind == "additive":
                for f in ("current_value", "baseline_value", "group_delta"):
                    arith.int_eq(gw + "." + f, e.get(f), o[f])
            else:
                for f in ("rate_effect", "mix_effect"):
                    arith.float_eq(gw + "." + f, e.get(f), o.get(f))
        arith.other(where + ":totals_keys", sorted(eb["totals"]), sorted(ob["totals"]), "keys")
        for f in ob["totals"]:
            arith.float_eq(where + ":totals." + f, eb["totals"].get(f), ob["totals"][f])
        flag_t.disputed_band += ob.get("tol_values_in_disputed_band", 0)
        flag_t.other(where + ":flag_keys", sorted(eb["flags"]), sorted(ob["flags"]), "keys")
        for f, value in ob["flags"].items():
            if f in ob["ambiguous_flags"]:
                flag_t.flags_ambiguous += 1
                continue
            flag_t.flags += 1
            flag_t.other(where + ":flags." + f, eb["flags"].get(f), value, "flag")


# ------------------------------------------------------------------ 예측


def _bd(result, dims):
    for b in result["breakdowns"]:
        if b["dimensions"] == dims:
            return b
    raise KeyError(dims)


def predictions(engine):
    out = {}
    e1e2 = [k for k in engine if k.startswith(("E1/", "E2/"))]
    refused = [k for k in e1e2 if engine[k]["error"]]
    out["P1"] = {"hit": not refused, "observed": {"runs": sorted(e1e2), "refused": refused}}

    def ok(key):
        return engine[key]["result"] is not None

    if ok("E1/complaint_count"):
        cross = _bd(engine["E1/complaint_count"]["result"], ["product", "issue"])
        out["P2"] = {"hit": cross["status"] == "ok", "observed": {
            "status": cross["status"], "cells": (cross.get("observed_cells")
                                                 if cross["status"] != "ok" else len(cross["groups"]))}}
    if ok("E1/timely_rate"):
        prod = _bd(engine["E1/timely_rate"]["result"], ["product"])
        out["P3"] = {"hit": prod["flags"]["decomposition_complete"] is True,
                     "observed": {"flags": prod["flags"],
                                  "non_comparable_groups": prod["non_comparable_groups"],
                                  "entry_exit_effect": prod["totals"]["entry_exit_effect"]}}
    if ok("E2/timely_rate"):
        prod = _bd(engine["E2/timely_rate"]["result"], ["product"])
        out["P4"] = {"hit": (prod["flags"]["decomposition_complete"] is False
                             and prod["flags"]["suppress_top_contributor"] is True),
                     "observed": {"flags": prod["flags"],
                                  "non_comparable_group_count": len(prod["non_comparable_groups"]),
                                  "entry_exit_effect": prod["totals"]["entry_exit_effect"]}}
    if ok("E2/complaint_count"):
        prod = _bd(engine["E2/complaint_count"]["result"], ["product"])
        entered = [g["key"]["product"] for g in prod["groups"] if g["baseline_value"] == 0]
        exited = [g["key"]["product"] for g in prod["groups"] if g["current_value"] == 0]
        warn = sorted(M.warnings_of(prod))
        # P5 판정: 제품 분해에 어떤 경고도 서지 않았다(그리고 실제로 제품이 들고 났다는 전제 확인).
        out["P5"] = {"hit": not warn, "premise_entry_or_exit_present": bool(entered or exited),
                     "observed": {"flags": prod["flags"], "warnings": warn,
                                  "products_entered": entered, "products_exited": exited}}
    if ok("E3/complaint_count"):
        res = engine["E3/complaint_count"]["result"]
        cross = _bd(res, ["company", "product"])
        comp = _bd(res, ["company"])
        out["P6"] = {"hit": (cross["status"] == "omitted"
                             and cross.get("reason") == "cross_cell_limit_exceeded"),
                     "observed": {"status": cross["status"], "reason": cross.get("reason"),
                                  "observed_cells": cross.get("observed_cells")}}
        out["P7"] = {"hit": comp["status"] == "ok",
                     "observed": {"status": comp["status"], "groups": len(comp.get("groups", []))}}
    for p in ("P2", "P3", "P4", "P5", "P6", "P7"):
        out.setdefault(p, {"hit": False, "observed": "prerequisite run refused"})
    return out


# ------------------------------------------------------------------ 손상


def read_rows(path, window_days):
    with open(path, "r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        header = list(reader.fieldnames)
        rows = [r for r in reader if any(a <= r["day"] <= b for a, b in window_days)]
    return rows, header


def write_rows(path, rows, header):
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header, lineterminator="\n")
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


def run_mutations(e1_full, acquisition):
    e1 = next(x for x in S.EXPERIMENTS if x["id"] == "E1")
    base_rows, header = read_rows(os.path.join(DERIVED, e1["file"]), [e1["baseline"], e1["current"]])
    out = {"base_rows": len(base_rows), "mutations": []}
    tmp = tempfile.mkdtemp(prefix="bia-cfpb-mut-")
    clean_path = os.path.join(tmp, "clean.csv")
    write_rows(clean_path, base_rows, header)
    clean = {m: guarded_run(engine_run, clean_path, e1, m) for m in (M.COUNT, M.RATE)}
    out["clean_subset_equals_full_file_run"] = {
        m: M.values_equal(clean[m]["result"], e1_full[m]["result"]) for m in clean}

    for mid, layer, expected in M.TABLE:
        entry = {"id": mid, "layer": layer, "expected": expected, "runs": {}}
        if mid == "M1":
            raw = acquisition
            stream, counter = M.m1_stream(read_allowlisted(raw["raw_path_outside_repo"]))
            outcome = guarded_run(lambda s: derive(s)[0], stream)
            outcome["touched"] = counter["injected"]
            entry["applied"] = dict(counter)
            if outcome["error"] is None:
                clean_pi = derive(read_allowlisted(raw["raw_path_outside_repo"]))[0]
                outcome["result"] = {"derived_rows_equal_clean": outcome["result"] == clean_pi}
                actual = M.VALID_CORRECT if outcome["result"]["derived_rows_equal_clean"] else M.SILENT_WRONG
            else:
                actual = M.classify(outcome, {})
            if outcome.get("error") and isinstance(outcome.get("message"), str):
                entry["loader_reasons"] = outcome["message"]
            entry["runs"]["loader"] = {"actual": actual, "error": outcome["error"],
                                       "graded": True}
        else:
            rows, hdr, applied = M.ENGINE_MUTATIONS[mid](base_rows, header)
            entry["applied"] = applied
            path = os.path.join(tmp, "%s.csv" % mid)
            write_rows(path, rows, hdr)
            truth = clean
            if mid == "M11":
                truth_rows, info = M.m11_truth(base_rows, header)
                entry["truth_info"] = info
                truth_path = os.path.join(tmp, "M11_truth.csv")
                write_rows(truth_path, truth_rows, header)
                truth = {m: guarded_run(engine_run, truth_path, e1, m) for m in (M.COUNT, M.RATE)}
            for metric in (M.COUNT, M.RATE):
                outcome = guarded_run(engine_run, path, e1, metric)
                outcome["touched"] = applied["touched"]
                actual = M.classify(outcome, truth[metric]["result"])
                run = {"actual": actual, "graded": metric in expected, "error": outcome["error"],
                       "stage": outcome.get("stage"), "message": outcome.get("message")}
                if outcome["result"] is not None:
                    run["new_warnings"] = M.new_warnings(outcome["result"], truth[metric]["result"])
                    run["diff_vs_truth"] = M.diff_summary(outcome["result"], truth[metric]["result"])
                    if mid == "M11":
                        run["diff_vs_clean"] = M.diff_summary(outcome["result"], clean[metric]["result"])
                entry["runs"][metric] = run
        for metric, run in entry["runs"].items():
            if run["graded"]:
                exp = expected[metric]
                run["expected"] = exp
                run["match"] = run["actual"] == exp
                run["defense_failure"] = M.is_defense_failure(exp, run["actual"])
        out["mutations"].append(entry)
    return out


# ------------------------------------------------------------------ 요약


def summary_md(meta, preds, a, muts):
    lines = ["# CFPB 외부 검증 — 실행 요약", "",
             "run.py 가 생성한다. 손으로 고치지 않는다. 사전등록: `../preregistration.md`.", "",
             "- HEAD: `%s`" % meta["guard"]["head"],
             "- 범위 가드(`%s` 비어 있음): %s" % (meta["guard"]["command"], meta["guard"]["diff_empty"]),
             "- oracle 프로세스에 로드된 bia 모듈 수: %d" % len(meta["oracle_bia_modules_loaded"]),
             "- 파생 파일 해시 = derivation.json 기록: %s" % meta["derived_sha_match"], "",
             "## 예측 P1~P7", "", "| # | 적중 | 관측 |", "|---|---|---|"]
    for p in sorted(preds, key=lambda x: int(x[1:])):
        obs = json.dumps(preds[p]["observed"], ensure_ascii=False)
        if len(obs) > 220:
            obs = obs[:220] + "…"
        lines.append("| %s | %s | `%s` |" % (p, "적중" if preds[p]["hit"] else "빗나감",
                                              obs.replace("|", "\\|")))
    lines += ["", "## 합격 기준 A1~A4", "", "| # | 결과 | 근거 |", "|---|---|---|"]
    for k in ("A1", "A2", "A3", "A4"):
        lines.append("| %s | %s | %s |" % (k, "통과" if a[k]["pass"] else "실패", a[k]["note"]))
    lines += ["", "## 손상 M1~M14", "",
              "| # | 지표 | 적용 행 | 기대 | 실제 | 일치 | 방어 실패 |", "|---|---|---|---|---|---|---|"]
    for m in muts["mutations"]:
        for metric, run in m["runs"].items():
            if not run["graded"]:
                continue
            lines.append("| %s | %s | %s | %s | %s | %s | %s |" % (
                m["id"], metric, m["applied"].get("touched", m["applied"].get("injected")),
                run["expected"], run["actual"], "예" if run["match"] else "아니오",
                "**예**" if run["defense_failure"] else "-"))
    lines += ["", "### 채점하지 않은 보조 실행", "", "| # | 지표 | 실제 |", "|---|---|---|"]
    for m in muts["mutations"]:
        for metric, run in m["runs"].items():
            if not run["graded"]:
                lines.append("| %s | %s | %s |" % (m["id"], metric, run["actual"]))
    lines.append("")
    return "\n".join(lines)


def main():
    with open(os.path.join(RESULTS, "acquisition.json"), encoding="utf-8") as handle:
        acquisition = json.load(handle)
    with open(os.path.join(RESULTS, "derivation.json"), encoding="utf-8") as handle:
        derivation = json.load(handle)
    sha_match = all(sha256_file(os.path.join(HERE, f["path"])) == f["sha256"]
                    for f in derivation["files"].values())
    if not sha_match:
        print("STOP: derived files do not match derivation.json")
        return 11

    oracle = run_oracle()
    meta = {"guard": guard, "python": platform.python_version(),
            "oracle_bia_modules_loaded": oracle["bia_modules_loaded"],
            "oracle_independent": not oracle["bia_modules_loaded"], "derived_sha_match": sha_match}
    if oracle["bia_modules_loaded"]:
        print("STOP: oracle process loaded bia modules: %s" % oracle["bia_modules_loaded"])
        write_json("run_meta.json", meta)
        return 12

    S.register_all()
    engine = {}
    for experiment in S.EXPERIMENTS:
        for metric in experiment["metrics"]:
            engine["%s/%s" % (experiment["id"], metric)] = guarded_run(
                engine_run, os.path.join(DERIVED, experiment["file"]), experiment, metric)
    for key, run in engine.items():
        write_json("engine_%s.json" % key.replace("/", "_"), run)

    arith, flag_t, cross_rows = Tally(), Tally(), []
    for key in engine:
        compare(key, engine[key], oracle["results"][key], arith, flag_t, cross_rows)
    preds = predictions(engine)
    muts = run_mutations({m: engine["E1/%s" % m] for m in ("complaint_count", "timely_rate")},
                         acquisition)

    graded = [r for m in muts["mutations"] for r in m["runs"].values() if r["graded"]]
    invalid = [m["id"] for m in muts["mutations"]
               for r in m["runs"].values() if r["graded"] and r["actual"] == M.INVALID]
    defense = ["%s/%s" % (m["id"], k) for m in muts["mutations"] for k, r in m["runs"].items()
               if r.get("defense_failure")]
    mismatched = ["%s/%s" % (m["id"], k) for m in muts["mutations"] for k, r in m["runs"].items()
                  if r["graded"] and not r["match"]]
    acceptance = {
        "A1": {"pass": not arith.mismatches,
               "note": "정수 %d · 실수 %d 비교, 불일치 %d, 최대 실수 차 %.3g" % (
                   arith.ints, arith.floats, len(arith.mismatches), arith.max_float_diff)},
        "A2": {"pass": not flag_t.mismatches,
               "note": "플래그 %d 비교, 불일치 %d, 모호 제외 %d" % (
                   flag_t.flags, len(flag_t.mismatches), flag_t.flags_ambiguous)},
        "A3": {"pass": all(r["match"] for r in cross_rows),
               "note": "교차 분해 %d, 규칙 일치 %d" % (len(cross_rows),
                                                  sum(r["match"] for r in cross_rows))},
        "A4": {"pass": not defense and not invalid,
               "note": "채점 %d, 기대 일치 %d, 불일치 %d, 방어 실패 %d (%s), 무효 %d" % (
                   len(graded), sum(r["match"] for r in graded), len(mismatched), len(defense),
                   ", ".join(defense) or "-", len(invalid))},
    }
    write_json("run_meta.json", meta)
    write_json("oracle_comparison.json", {
        "arithmetic": {"ints": arith.ints, "floats": arith.floats,
                       "max_float_diff": arith.max_float_diff, "mismatches": arith.mismatches[:200],
                       "mismatch_count": len(arith.mismatches)},
        "flags": {"compared": flag_t.flags, "ambiguous_excluded": flag_t.flags_ambiguous,
                  "tol_values_in_disputed_band_1e9_2e9": flag_t.disputed_band,
                  "mismatches": flag_t.mismatches[:200], "mismatch_count": len(flag_t.mismatches)},
        "cross": cross_rows})
    write_json("predictions.json", preds)
    write_json("mutations.json", muts)
    write_json("acceptance.json", {"acceptance": acceptance, "mismatched_mutations": mismatched,
                                   "defense_failures": defense, "invalid_mutations": invalid})
    with open(os.path.join(RESULTS, "summary.md"), "w", encoding="utf-8") as handle:
        handle.write(summary_md(meta, preds, acceptance, muts))
    print(json.dumps(acceptance, ensure_ascii=False, indent=1))
    print("predictions:", {k: v["hit"] for k, v in sorted(preds.items())})
    return 0


if __name__ == "__main__":
    sys.exit(main())
