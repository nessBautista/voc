"""Terminal rendering of storage reports; no requests or filesystem writes."""

import json


def _text(value):
    # S3 keys may contain terminal control characters; show them literally.
    return json.dumps(str(value), ensure_ascii=False)[1:-1]


def _summary(branch):
    if branch["status"] != "ok":
        return f"[{branch['name']}: {branch['status']}] {_text(branch['error']['message'])}"
    text = f"[{branch['name']}: listed {branch['listed_objects']} objects, {branch['listed_bytes']} bytes]"
    if branch["is_truncated"]:
        text += " [more objects available]"
    elif branch["listed_objects"] == 0:
        text += " [empty page]"
    return text


def _tree(report, depth):
    root = {"children": {}, "labels": []}

    def insert(parts):
        node = root
        for part in parts:
            node = node["children"].setdefault(part, {"children": {}, "labels": []})
        return node

    unavailable = []
    for branch in report["scopes"]:
        if branch["prefix"] is None:
            unavailable.append(_summary(branch))
            continue
        base = branch["prefix"].rstrip("/").split("/")
        insert(base)["labels"].append(_summary(branch))
        for item in branch["objects"]:
            relative = item["relative_key"].split("/")
            node = insert(base + relative[:depth])
            if len(relative) > depth:
                if "[collapsed]" not in node["labels"]:
                    node["labels"].append("[collapsed]")
            else:
                node["labels"].append(f"{item['bytes']} bytes")
    lines = [f"s3://{report['bucket']}/"]

    def walk(node, indent):
        children = sorted(node["children"].items())
        for index, (name, child) in enumerate(children):
            last = index == len(children) - 1
            directory = bool(child["children"]) or any(
                label.startswith("[") for label in child["labels"]
            )
            label = _text(name) + ("/" if directory else "")
            lines.append(
                indent
                + ("└── " if last else "├── ")
                + label
                + " "
                + " ".join(child["labels"])
            )
            walk(child, indent + ("    " if last else "│   "))

    walk(root, "")
    return lines + unavailable


def render_report(report, *, format="tree", depth=2):
    """Render only the returned page; depth never causes extra S3 crawling."""
    lines = [
        f"Bucket: {report['bucket']} | Region: {report['region']}",
        f"Observed: {report['observed_at']} | Result: {report['state']}",
    ]
    if format == "tree":
        lines.extend(_tree(report, depth))
    else:
        for branch in report["scopes"]:
            lines.extend([branch["uri"] or branch["name"], _summary(branch)])
            if branch["objects"]:
                lines.append("Relative key\tBytes\tModified\tStorage class")
            for item in branch["objects"]:
                lines.append(
                    "\t".join(
                        _text(item[key])
                        for key in (
                            "relative_key",
                            "bytes",
                            "last_modified",
                            "storage_class",
                        )
                    )
                )
    for branch in report["scopes"]:
        if branch["next_continuation_token"]:
            lines.append(
                f"{branch['name']} next_continuation_token: {_text(branch['next_continuation_token'])}"
            )
    release = report["release"]
    if release:
        lines.extend(
            [
                f"Release: {release['embedding_release_id']} [{release['status']}]",
                f"Manifest: {release['manifest_uri']}",
                f"Manifest SHA-256: {release['manifest_sha256']}",
                f"Dataset: {release['dataset_release_id']} | Profile: {release['profile_id']}",
                f"Coverage: {json.dumps(release['coverage'], sort_keys=True)}",
                f"Selection: {release['selection']['kind'] if release['selection'] else None}",
                f"Latest: {release['latest_release_id']} | Matches release: {release['is_latest']}",
                f"Latest manifest checksum matches: {release['latest_manifest_checksum_matches']}",
            ]
        )
        for artifact in release["artifacts"]:
            lines.append(
                f"{artifact['name']}: present={artifact['present']}, bytes={artifact['observed_bytes']} (declared {artifact['declared_bytes']})"
            )
        lines.append(
            "Artifact content verified: false (metadata/presence/size checks only)"
        )
        for issue in release["issues"]:
            lines.append(
                f"[{issue['status']}] {_text(issue['operation'])}: {_text(issue['message'])}"
            )
    return "\n".join(lines)
