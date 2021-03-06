import argparse
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.model_selection import cross_val_score, ShuffleSplit, ParameterGrid
from sklearn.feature_selection import RFECV
import pickle
import numpy as np
import pandas as pd
import json
import os
import string
import sys
import csv
from itertools import product, groupby
from functools import partial
from multiprocessing import Pool
from typing import Dict, Tuple

sys.path.append("../")
from foldit_data import load_extend_data, make_series
from util import PatternLookup, SubClusters, SubclusterSeries, SubSeriesLookup
from check_models import load_sub_lookup
from pattern_extraction import combine_user_series, get_predicted_lookups, get_pattern_lookups, score_param, load_TICC_output, get_pattern_label

IGNORE_COLUMNS = ['energies', 'evol_lines', 'first_pdb', 'frontier_pdbs', 'frontier_tmscores', 'lines',
                  'pid', 'timestamps', 'uid', 'upload_rate', 'upload_ratio', 'deltas', 'relevant_sids']


def compute_pattern_actions(r: pd.Series, k: int, cid: int, sub_k: int,
                            cluster_lookup: Dict[int, np.ndarray],
                            subcluster_series: SubclusterSeries,
                            puz_idx_lookup:  Dict[Tuple[str, str], Tuple[int, int]]) -> pd.Series:
    if sub_k == 0:
        puz_cs = cluster_lookup[k][slice(*puz_idx_lookup[(r.uid, r.pid)])]
        return pd.Series(sum(r.actions[puz_cs == cid]), index=["pattern_{}_actions".format(cid)])
    else:
        puz_cs = subcluster_series.series[slice(*puz_idx_lookup[(r.uid, r.pid)])]
        return pd.Series([sum(r.actions[puz_cs == scid]) for scid in subcluster_series.labels],
                         index=["pattern_{}_actions".format(l) for l in subcluster_series.labels])

def score_candidate(X, y, cv):
    selector = RFECV(GradientBoostingRegressor(loss="huber"), step=1, cv=cv)
    selector.fit(X, y)
    X_sel = selector.transform(X)
    # score for this candidate is CV score fitting on X_sel
    return (np.mean(cross_val_score(GradientBoostingRegressor(loss="huber"), X_sel, y, cv=cv)),
                    selector.get_support())


def find_best_predictive_model(model_dir: str, data: pd.DataFrame,
                               puz_idx_lookup:  Dict[Tuple[str, str], Tuple[int, int]],
                               pattern_lookups: Dict[int, PatternLookup],
                               cluster_lookup: Dict[int, np.ndarray],
                               subseries_lookups: Dict[int, Dict[int, SubSeriesLookup]],
                               subclusters: SubClusters,
                               action_counts) -> Tuple:
    """
    BRUTE FORCE APPROACH, memory needs are too high
    1. for each candidate (choice of k and sub-ks)
        1a. compute the action count for each pattern/subpattern
            (build data structure like existing lookups for whole suite of action counts)
            (gradually assemble, computing new counts as needed)
        1b. score model using those action count features -> candidate score
    2. return the best-scoring candidate

    NEW APPROACH
    1. compute base model scores (no subpatterns)
    2.
    """


    # action_counts: (k, (cid, sub_k)) -> pd.DataFrame (single column for base pattern, column per subpattern otherwise)
    subcluster_series_lookup = {} # (k, (cid, sub_k)) -> SubclusterSeries

    cv = ShuffleSplit(n_splits=3, test_size=0.3, random_state=304)
    scores_lookup = {}
    for k in pattern_lookups:
        scores = {}  # candidate tuple -> CV score, selected features mask
        cids = {p.cid for p in pattern_lookups[k]["base"]}
        candidates = [c for c in product(*[list(product([cid], [0] + list(subclusters[k][cid].keys())))
                                           for cid in cids])
                      if all(sub_k in pattern_lookups[k][cid] for cid, sub_k in c)]
        with Pool() as pool:
            for candidate in candidates:

                print("candidate", candidate)
                for (cid, sub_k) in candidate:

                    if sub_k != 0 and (k, (cid, sub_k)) not in subcluster_series_lookup:
                        print("subcluster serires", cid, sub_k)
                        all_subclusters = cluster_lookup[k].astype(np.str)
                        labels = ["{}{}".format(cid, string.ascii_uppercase[x]) for x in range(sub_k)]
                        cs = subclusters[k][cid][sub_k]
                        for (_, _, start_idx), (s, e) in subseries_lookups[k][cid].idx_lookup.items():
                            all_subclusters[start_idx: start_idx + (min(e, len(cs)) - s)] = [labels[c] for c in cs[s:e]]
                        subcluster_series_lookup[(k, (cid, sub_k))] = SubclusterSeries(labels, all_subclusters)

                    if (k, (cid, sub_k)) not in action_counts:
                        print("action counts", cid, sub_k)
                        f = partial(compute_pattern_actions, k=k, cid=cid, sub_k=sub_k, cluster_lookup=cluster_lookup,
                                    subcluster_series=subcluster_series_lookup.get((k, (cid, sub_k)), None),
                                    puz_idx_lookup=puz_idx_lookup)
                        action_counts[(k, (cid, sub_k))] = data.apply(f, axis=1)

                features = pd.concat([data.drop(IGNORE_COLUMNS + ["time", "actions"], axis=1)] +
                                     [action_counts[(k, (cid, sub_k))] for (cid, sub_k) in candidate],
                                     axis=1)
                X = features.drop(["perf"], axis=1).values
                y = features["perf"].values.ravel()
                scores[candidate] = pool.apply_async(score_candidate, (X, y, cv))
            print("scoring k =", k, "candidates... ")
            i = 0
            for candidate, x in scores.items():
                scores[candidate] = x.get()
                i += 1
                print("{} out of {}\r".format(i, len(scores)), end="")
            print("done\n\n\n")
            scores_lookup[k] = scores
            with open("{}/eval/{}_scores.pickle".format(model_dir, k), "wb") as fp:
                pickle.dump(scores, fp)

    best_k = best_candidate = None
    best_score = 0
    for k, scores in scores_lookup.items():
        for candidate, (score, support) in scores.items():
            if score > best_score:
                best_score = score
                best_k = k
                best_candidate = candidate
    return best_k, best_candidate, scores_lookup


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog='pattern_model_evaluation.py')
    parser.add_argument("model_dirs", nargs="+")
    args = parser.parse_args()
    assert all(os.path.exists(model_dir) for model_dir in args.model_dirs)

    print("loading raw data", end="...")
    pids = ["2003433", "2003642", "2003195", "2003313", "2003287", "2002475", "2002294", "2002196", "2002141", "2002110"]
    soln_lookup = {}
    parent_lookup = {}
    child_lookup = {}
    data, puz_metas = load_extend_data(pids, soln_lookup, parent_lookup, child_lookup, False, 600)

    with open("../data/foldit/user_metadata_v4.csv") as fp:
        user_metas = {(r['uid'], r['pid']): r for r in csv.DictReader(fp)}
        for v in user_metas.values():
            v['time'] = int(v['time'])
            v['relevant_time'] = int(float(v['relevant_time']))
            v['best_energy_time'] = int(v['best_energy_time'])
            v['action_count_all'] = int(v['action_count_all'])
            v['action_count_relevant'] = int(v['action_count_relevant'])
            v['action_count_best'] = int(v['action_count_best'])
            v['best_energy'] = float(v['best_energy'])
            v['perf'] = float(v['perf'])
            v['solo_perf'] = float(v['solo_perf']) if v['solo_perf'] != "" else np.nan
    user_meta_lookup = {uid: list(metas) for uid, metas in groupby(sorted(user_metas.values(), key=lambda m: m['uid']), lambda m: m['uid'])}

    with open("../data/foldit/puz_metadata_v4.csv") as fp:
        puz_infos = {r['pid']: {'start':     int(r['start']),
                                'end':       int(r['end']),
                                'baseline':  float(r['baseline']),
                                'best':      float(r['best']),
                                'best_solo': float(r['best_solo'])
                               } for r in csv.DictReader(fp)}
    print("done")

    for model_dir in args.model_dirs:
        print("evaluating model at", model_dir)
        print("loading model data", end="...")
        noise = np.loadtxt(model_dir + "/noise_values.txt")
        puz_idx_lookup, series_lookup, _ = make_series(data, noise=noise)
        idx_lookup, all_series = combine_user_series(series_lookup, noise)
        puz_idx_lookup = {(uid, pid): (s + idx_lookup[uid][0], e + idx_lookup[uid][0])
                          for (uid, pid), (s, e) in puz_idx_lookup.items()}

        with open(model_dir + "/config.json") as fp:
            config = json.load(fp)
        krange = config["krange"]
        _, mrf_lookup, model_lookup, _ = load_TICC_output(model_dir, ["all"], krange)
        dummy_subseries_lookup = {int(d.strip("k")): [int(c.strip("cid")) for c in os.listdir(model_dir + "/all/subpatterns/" + d)]
                                  for d in os.listdir(model_dir + "/all/subpatterns") if d.startswith("k")}
        sub_lookup = load_sub_lookup(model_dir + "/all", dummy_subseries_lookup, [3, 6, 9, 12])

        if os.path.exists(model_dir + "/eval/cluster_lookup.pickle"):
            with open(model_dir + "/eval/cluster_lookup.pickle", "rb") as fp:
                cluster_lookup = pickle.load(fp)
            with open(model_dir + "/eval/subseries_lookup.pickle", "rb") as fp:
                subseries_lookups = pickle.load(fp)
            with open(model_dir + "/eval/sub_clusters.pickle", "rb") as fp:
                subclusters = pickle.load(fp)
            with open(model_dir + "/eval/pattern_lookup.pickle", "rb") as fp:
                pattern_lookups = pickle.load(fp)
        else:
            # predict patterns on full data for all candidate models
            print("generating patterns on full data", end="...")
            cluster_lookup, subseries_lookups, subclusters = get_predicted_lookups(all_series, krange, model_lookup["all"],
                                                                                   sub_lookup.models, mrf_lookup["all"],
                                                                                   puz_idx_lookup, noise)
            pattern_lookups = get_pattern_lookups(krange, subclusters, sub_lookup.mrfs, subseries_lookups, cluster_lookup,
                                                  mrf_lookup["all"], puz_idx_lookup)
            os.makedirs(model_dir + "/eval", exist_ok=True)
            with open(model_dir + "/eval/cluster_lookup.pickle", "wb") as fp:
                pickle.dump(cluster_lookup, fp)
            with open(model_dir + "/eval/subseries_lookup.pickle", "wb") as fp:
                pickle.dump(subseries_lookups, fp)
            with open(model_dir + "/eval/sub_clusters.pickle", "wb") as fp:
                pickle.dump(subclusters, fp)
            with open(model_dir + "/eval/pattern_lookup.pickle", "wb") as fp:
                pickle.dump(pattern_lookups, fp)
        print("done")
        # select model
        print("generating action count series", end="...")
        rows = []
        for _, r in data.iterrows():
            if r.relevant_sids is None or (r.uid, r.pid) not in puz_idx_lookup:
                continue
            deltas = sorted([d for d in r.deltas if d.sid in r.relevant_sids], key=lambda x: x.timestamp)
            actions = np.array([sum(d.action_diff.values()) for d in deltas])
            if actions.sum() == 0:
                # logging.debug("SKIPPING {} {}, no actions recorded".format(r.uid, r.pid))
                continue
            rows.append({'uid': r.uid, 'pid': r.pid, "actions": actions})
        data = data.merge(pd.DataFrame(data=rows), on=["pid", "uid"])
        assert len(rows) == len(data)  # check that data consists of only rows with an actions column

        data["experience"] = data.apply(lambda r: len([x for x in user_meta_lookup[r.uid]
                                                            if puz_infos[x['pid']]["end"] < puz_infos[r.pid]["start"]]), axis=1)
        data["median_prior_perf"] = data.apply(lambda r: np.median([float(x['perf']) for x in user_meta_lookup[r.uid]
                                                                         if puz_infos[x['pid']]["end"] < puz_infos[r.pid]["start"]]), axis=1)
        data.median_prior_perf.fillna(data.median_prior_perf.median(), inplace=True)
        print("done")

        print("finding most predictive model")
        action_counts = {}
        best_k, best_candidate, scores_lookup = find_best_predictive_model(model_dir, data, puz_idx_lookup, pattern_lookups,
                                                                           cluster_lookup, subseries_lookups, subclusters,
                                                                           action_counts)
        with open(model_dir + "/eval/best_model.txt", 'w') as fp:
            fp.write(str((best_k, best_candidate)) + "\n")
        with open(model_dir + "/eval/action_counts.pickle", "wb") as fp:
            pickle.dump(action_counts, fp)
        print("selected model:", best_k, best_candidate)

        ps = sum([[(get_pattern_label(p, cid, sub_k), p) for p in pattern_lookups[best_k][cid][sub_k]] for cid, sub_k in best_candidate], [])
        ps_uid_pid = {tag: sorted(xs) for tag, xs in groupby(sorted(ps, key=lambda p: (p[1].uid, p[1].pid)), lambda p: (p[1].uid, p[1].pid))}
        pattern_use_lookup = {tag: {pt for pt, _ in xs} for tag, xs in ps_uid_pid.items()}
        pts = {pt for pt, p in ps}

        # collect pattern features using selected model
        results = pd.concat([data] + [action_counts[(best_k, (cid, sub_k))] for (cid, sub_k) in best_candidate], axis=1)

        pattern_features = ["pattern_{}".format(pt) for pt in pts]

        acc = []
        for (uid, pid), use in pattern_use_lookup.items():
            r = {"uid": uid, "pid": pid}
            for pt in pts:
                r["pattern_"+ pt+"_use"] = 1 if pt in use else 0
            acc.append(r)
        results = results.merge(pd.DataFrame(data=acc), on=["uid", "pid"])
        #results["distinct_patterns"] = results.apply(lambda r: len(pattern_use_lookup[(r.uid, r.pid)]), axis=1)
        #results["action_count_all"] = results.apply(lambda r: user_metas[(r.uid, r.pid)]["action_count_all"], axis=1)
        results["action_count_relevant"] = results.apply(lambda r: user_metas[(r.uid, r.pid)]["action_count_relevant"], axis=1)
        #results["action_count_best"] = results.apply(lambda r: user_metas[(r.uid, r.pid)]["action_count_best"], axis=1)
        #results["best_energy_time"] = results.apply(lambda r: user_metas[(r.uid, r.pid)]["best_energy_time"], axis=1)
        #results["action_rate_all"] = results.apply(lambda r: r.action_count_all / r.time, axis=1)
        #results["action_rate_relevant"] = results.apply(lambda r: r.action_count_relevant / r.relevant_time, axis=1)


        # find best model, compare to baseline

        features = results.drop(IGNORE_COLUMNS, axis=1)

        baseline_features = ["action_count_relevant", "median_prior_perf", "experience"]

        seed = 13*17*31
        models = {#"ridge": Ridge,
                  "ensemble": GradientBoostingRegressor}
        model_params = {"ridge": {"random_state": [seed], "alpha": [0.1, 0.5, 1, 5, 10], "normalize": [True, False]},
                        "ensemble": {"random_state": [seed], "learning_rate": [0.01, 0.02, 0.05, 0.1], "subsample": [0.3, 0.5, 0.7],
                                     "n_estimators": [100, 500, 1000], "n_iter_no_change": [100]}}
        # std_base = deepcopy(model_params["ensemble"])
        # std_base["loss"] = ["ls", "lad"]
        # huber_base = deepcopy(model_params["ensemble"])
        # huber_base["loss"] = ["huber"]
        # huber_base["alpha"] = [0.9, 0.95, 0.99]
        # model_params["ensemble"] = [std_base, huber_base]
        model_params["ensemble"]["loss"] = ["huber"]
        model_params["ensemble"]["alpha"] = [0.85, 0.9, 0.95, 0.99]

        with Pool(50, maxtasksperchild=4) as pool:
            cv = ShuffleSplit(n_splits=3, test_size=0.3, random_state=seed)
            print("fitting baseline")
            scores = {}
            X = features[baseline_features].values
            y = features["perf"].values.ravel()
            for lab, model in models.items():
                evals = []
                for param in ParameterGrid(model_params[lab]):
                    evals.append((pool.apply_async(score_param, (param, model, X, y, cv)), param))
                print("{}: {} scores sent to pool, collecting results".format(lab, len(evals)))
                scores[lab] = [(x.get(), param) for x, param in evals]
            baseline_scores = scores
            print("baseline")
            print(max(scores["ensemble"], key=lambda x: x[0][0]))

            model_scores = {}
            for ftype in ["actions", "use"]:
                print("fitting pattern {} models".format(ftype))
                candidate_features = ["median_prior_perf", "experience"] + ["{}_{}".format(f, ftype) for f in pattern_features]
                if ftype == "use":
                    candidate_features.append("action_count_relevant")
                scores = {}
                X = features[candidate_features].values
                y = features["perf"].values.ravel()
                for lab, model in models.items():
                    evals = []
                    for param in ParameterGrid(model_params[lab]):
                        evals.append((pool.apply_async(score_param, (param, model, X, y, cv)), param))
                    print("{}: {} scores sent to pool, collecting results".format(lab, len(evals)))
                    scores[lab] = [(x.get(), param) for x, param in evals]
                model_scores[ftype] = scores
                print("best {} model".format(ftype))
                print(max(scores["ensemble"], key=lambda x: x[0][0]))

        with open(model_dir + "/eval/baseline_scores.pickle", 'wb') as fp:
            pickle.dump(baseline_scores, fp)
        with open(model_dir + "/eval/model_scores.pickle", 'wb') as fp:
            pickle.dump(model_scores, fp)
