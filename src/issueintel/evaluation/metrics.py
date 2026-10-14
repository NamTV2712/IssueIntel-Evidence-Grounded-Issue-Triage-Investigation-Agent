"""Diagnostic localization metrics; no inferred human/causal correctness."""


def localization_metrics(ranked_paths: list[str], gold_files, *, k: int = 10) -> dict:
    if type(k) is not int or k <= 0:
        raise ValueError("k must be positive")
    gold = set(gold_files)
    unique = list(dict.fromkeys(ranked_paths))
    selected = set(unique[:k])
    if not gold:
        return {"recall_at_k": None, "precision_at_k": None, "all_files_at_k": None, "mrr": None,
                "gold_files": 0, "status": "undefined_no_gold"}
    first = next((index for index, path in enumerate(unique, 1) if path in gold), None)
    return {"recall_at_k": len(selected & gold) / len(gold),
            "precision_at_k": len(selected & gold) / len(selected) if selected else 0,
            "all_files_at_k": int(gold.issubset(selected)), "mrr": 1 / first if first else 0,
            "gold_files": len(gold), "retrieved_files": len(selected),
            "denominator": "all_patch_files_including_unavailable", "status": "patch_localization_proxy"}
