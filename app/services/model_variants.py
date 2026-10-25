"""Group the checkpoints of one model family and say which copies are spare.

A family such as MiniMax H3 ships several interchangeable 19 GB checkpoints:
Pruned, Fused Turbo, Singularity, FL2VA, community merges. Nothing told the
user which of those files Maestro actually loads, so the disk filled up with
copies nobody could identify.
"""
from __future__ import annotations


def build_family_plan(models) -> list[dict]:
    """Return families that hold more than one installed checkpoint.

    ``models`` is a list of ``{model_type, model_name, group, group_label,
    filename, size_bytes, path, in_use, modified}``. The copy an in-use model
    resolves to is kept; otherwise the most recently written file is kept.
    Everything else is offered as spare and reports which model entries own it
    so the user recognises the name.
    """

    families: dict[str, dict] = {}
    for model in models or []:
        if not isinstance(model, dict):
            continue
        filename = str(model.get("filename") or "")
        group = str(model.get("group") or "")
        if not filename or not group:
            continue
        family = families.setdefault(group, {
            "group": group,
            "label": str(model.get("group_label") or group),
            "files": {},
        })
        files = family["files"]
        entry = files.setdefault(filename, {
            "filename": filename,
            "size_bytes": int(model.get("size_bytes") or 0),
            "path": model.get("path"),
            "modified": float(model.get("modified") or 0.0),
            "models": [],
            "in_use": False,
        })
        entry["size_bytes"] = max(entry["size_bytes"], int(model.get("size_bytes") or 0))
        entry["modified"] = max(entry["modified"], float(model.get("modified") or 0.0))
        name = str(model.get("model_name") or model.get("model_type") or filename)
        if name not in entry["models"]:
            entry["models"].append(name)
        if bool(model.get("in_use")):
            entry["in_use"] = True

    groups = []
    for family in families.values():
        files = list(family["files"].values())
        if len(files) < 2:
            continue
        kept = next((item for item in files if item["in_use"]), None)
        if kept is None:
            kept = max(files, key=lambda item: (item["modified"], item["size_bytes"]))
        variants = []
        for item in files:
            variants.append({
                "filename": item["filename"],
                "size_bytes": item["size_bytes"],
                "path": item["path"],
                "models": item["models"],
                "removable": item["filename"] != kept["filename"],
            })
        reclaimable = sum(item["size_bytes"] for item in variants if item["removable"])
        if reclaimable <= 0:
            continue
        groups.append({
            "group": family["group"],
            "label": family["label"],
            "keep": {
                "filename": kept["filename"],
                "size_bytes": kept["size_bytes"],
                "models": kept["models"],
            },
            "variants": variants,
            "reclaimable_bytes": reclaimable,
        })

    groups.sort(key=lambda group: -group["reclaimable_bytes"])
    return groups


def total_reclaimable(groups) -> int:
    return sum(int(group.get("reclaimable_bytes") or 0) for group in groups or [])
