"""Merge-point identity: content-keyed, never branch names."""
import hashlib


def merge_point_id(base_tree_sha: str, member_patch_ids: list[str]) -> str:
    canon = base_tree_sha + ":" + ",".join(sorted(member_patch_ids))
    return hashlib.sha256(canon.encode()).hexdigest()
