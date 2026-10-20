"""Build the whole Shadow Box environment from scratch, idempotently.

Creates: metadata templates, App Users, a payments-team group, the folder tree,
collaborations (permissions), uploads every seed source + memory file, applies
seed metadata from the *.meta.json sidecars (no AI units), and writes ids.json.

Run after `python seed/generate_data.py`:
    python seed/setup_box.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from shadow_box import tools as T  # noqa: E402
from shadow_box.client import get_service_client  # noqa: E402

DATA = HERE / "data"
IDS_PATH = HERE / "ids.json"
API = "https://api.box.com/2.0"

EMAIL = "{}@payments.example".format
APP_USERS = [
    ("Alex Rivera", EMAIL("alex")),
    ("Priya Nair", EMAIL("priya")),
    ("Marcus Lee", EMAIL("marcus")),
    ("Dana Kim", EMAIL("dana")),
    ("Sam Ortiz", EMAIL("sam")),
    ("Jordan Blake", EMAIL("jordan")),
    ("Nina Patel", EMAIL("nina")),
]

COMPONENTS = ["retry-service", "payments-api", "ledger", "gateway-client", "checkout"]

# folder layout: name -> list of subfolders
FOLDERS = {
    "Inbox": [],   # service-only: uploads land here; only the Classifier reads it
    "Sources": ["Meetings", "Slack", "Issues", "PRs", "Docs", "Org"],
    "Memory": ["Meeting Notes", "Decisions", "People", "Handbook"],
    "Restricted": [],
    "Quarantine": [],
}

# per-agent App Users with LEAST-PRIVILEGE Box access. The map value lists the
# folder paths each agent may act on; apply_agent_permissions grants exactly
# those, so a prompt-injected agent still cannot touch anything outside its role
# — Box enforces it, not the prompt. (Brief §3.1/§3.3.)
AGENT_USERS = {
    # Classifier: reads the Inbox, writes metadata + promotes files out of it
    "Shadow Classifier": {
        # editor on Inbox: read a new file + move it out after classifying.
        # uploader on the destinations: it may DROP a file into them but NOT
        # read or modify what is already there — so an injected Classifier can't
        # read existing Restricted/Quarantine contents, only promote into them.
        "read": ["Inbox"],
        "write": ["Inbox"],
        "upload": ["Sources", "Restricted", "Quarantine"],
    },
    # Scribe: reads classified meetings, writes ONLY meeting notes
    "Shadow Scribe": {
        "read": ["Sources/Meetings"],
        "write": ["Memory/Meeting Notes"],
    },
    # Librarian: reads notes + decisions, writes ONLY decisions
    "Shadow Librarian": {
        "read": ["Memory/Meeting Notes", "Memory/Decisions"],
        "write": ["Memory/Decisions"],
    },
    # Cartographer: reads org + people, writes ONLY people cards
    "Shadow Cartographer": {
        "read": ["Sources/Org", "Memory/People"],
        "write": ["Memory/People"],
    },
}

# which seed area maps into which Box folder path
AREA_TO_PATH = {
    "Meetings": ["Sources", "Meetings"],
    "Slack": ["Sources", "Slack"],
    "Issues": ["Sources", "Issues"],
    "PRs": ["Sources", "PRs"],
    "Docs": ["Sources", "Docs"],
    "Org": ["Sources", "Org"],
    "Restricted": ["Restricted"],
    "Quarantine": ["Quarantine"],
}


def hdr():
    return T._headers()


# --------------------------------------------------------------------------- #
# metadata templates
# --------------------------------------------------------------------------- #
def ensure_templates():
    def opts(values):
        return [{"key": v} for v in values]

    templates = {
        "shadowItem": [
            {"key": "sourceType", "type": "enum",
             "options": opts(["meeting", "slack", "issue", "pr", "doc", "org"])},
            {"key": "title", "type": "string"},
            {"key": "occurredOn", "type": "date"},
            {"key": "components", "type": "multiSelect", "options": opts(COMPONENTS)},
            {"key": "issueIds", "type": "string"},
            {"key": "people", "type": "string"},
            {"key": "summary", "type": "string"},
            {"key": "sensitivity", "type": "enum",
             "options": opts(["normal", "restricted", "hr"])},
        ],
        "shadowDecision": [
            {"key": "decision", "type": "string"},
            {"key": "status", "type": "enum",
             "options": opts(["proposed", "decided", "paused", "resolved", "reversed"])},
            {"key": "decidedOn", "type": "date"},
            {"key": "owners", "type": "string"},
            {"key": "components", "type": "multiSelect", "options": opts(COMPONENTS)},
            {"key": "sourceFileIds", "type": "string"},
            {"key": "confirmed", "type": "enum", "options": opts(["yes", "pending"])},
        ],
        "shadowPerson": [
            {"key": "name", "type": "string"},
            {"key": "email", "type": "string"},
            {"key": "role", "type": "string"},
            {"key": "team", "type": "string"},
            {"key": "manager", "type": "string"},
            {"key": "active", "type": "enum", "options": opts(["yes", "no"])},
            {"key": "expertise", "type": "multiSelect", "options": opts(COMPONENTS)},
            {"key": "lastActiveOn", "type": "date"},
            {"key": "movedTo", "type": "string"},
            {"key": "movedOn", "type": "date"},
        ],
        # the agents' shared blackboard + idempotency key. `state` tracks a file
        # through the ingest pipeline; `processed_version` is the Box file
        # version last fully processed (so a replayed webhook for the same
        # version is a no-op); `last_error`/`attempts` support retry/visibility.
        "shadowPipeline": [
            {"key": "state", "type": "enum", "options": opts([
                "received", "classified", "noted", "decisions_checked",
                "people_updated", "done", "failed"])},
            {"key": "processedVersion", "type": "string"},
            {"key": "lastError", "type": "string"},
            {"key": "attempts", "type": "float"},
        ],
    }
    existing = requests.get(f"{API}/metadata_templates/enterprise",
                            headers=hdr(), timeout=30).json()
    have = {t["templateKey"] for t in existing.get("entries", [])}
    for key, fields in templates.items():
        if key in have:
            print(f"  template {key} exists")
            continue
        for f in fields:  # Box requires a displayName on every field
            f.setdefault("displayName", f["key"])
        body = {"scope": "enterprise", "templateKey": key, "displayName": key,
                "fields": fields}
        r = requests.post(f"{API}/metadata_templates/schema",
                          headers=hdr(), json=body, timeout=30)
        r.raise_for_status()
        print(f"  created template {key}")
    return list(templates.keys())


# --------------------------------------------------------------------------- #
# app users
# --------------------------------------------------------------------------- #
def ensure_agent_users() -> dict[str, str]:
    """Create the per-agent App Users (idempotent). Separate from the human demo
    users: these are the agent IDENTITIES whose Box access is scoped by role."""
    existing = requests.get(f"{API}/users?filter_term=&limit=1000",
                            headers=hdr(), timeout=30).json().get("entries", [])
    by_name = {u["name"]: u["id"] for u in existing}
    out: dict[str, str] = {}
    for name in AGENT_USERS:
        if name in by_name:
            out[name] = by_name[name]
            print(f"  agent user {name} exists ({by_name[name]})")
            continue
        r = requests.post(f"{API}/users", headers=hdr(),
                          json={"name": name, "is_platform_access_only": True},
                          timeout=30)
        r.raise_for_status()
        out[name] = r.json()["id"]
        print(f"  created agent user {name} ({out[name]})")
    return out


def ensure_app_users() -> dict[str, str]:
    existing = requests.get(f"{API}/users?filter_term=&limit=1000",
                            headers=hdr(), timeout=30).json().get("entries", [])
    by_name = {u["name"]: u["id"] for u in existing}
    out: dict[str, str] = {}
    for name, _login in APP_USERS:
        if name in by_name:
            out[name] = by_name[name]
            print(f"  app user {name} exists ({by_name[name]})")
            continue
        r = requests.post(f"{API}/users", headers=hdr(),
                          json={"name": name, "is_platform_access_only": True},
                          timeout=30)
        r.raise_for_status()
        out[name] = r.json()["id"]
        print(f"  created app user {name} ({out[name]})")
    return out


# --------------------------------------------------------------------------- #
# group
# --------------------------------------------------------------------------- #
def ensure_group(member_ids: list[str]) -> str:
    groups = requests.get(f"{API}/groups?limit=1000", headers=hdr(), timeout=30).json()
    gid = None
    for g in groups.get("entries", []):
        if g["name"] == "payments-team":
            gid = g["id"]
            break
    if gid is None:
        r = requests.post(f"{API}/groups", headers=hdr(),
                          json={"name": "payments-team"}, timeout=30)
        r.raise_for_status()
        gid = r.json()["id"]
        print(f"  created group payments-team ({gid})")
    else:
        print(f"  group payments-team exists ({gid})")
    # memberships (idempotent: ignore 409)
    for uid in member_ids:
        requests.post(f"{API}/group_memberships", headers=hdr(),
                      json={"user": {"id": uid}, "group": {"id": gid}}, timeout=30)
    return gid


# --------------------------------------------------------------------------- #
# folders
# --------------------------------------------------------------------------- #
def ensure_folder(name: str, parent_id: str) -> str:
    items = T.folder_items(parent_id)
    for it in items:
        if it["type"] == "folder" and it["name"] == name:
            return it["id"]
    return T.create_folder(name, parent_id)


def build_tree() -> dict:
    root = ensure_folder("Shadow - Payments Team", "0")
    ids = {"root": root, "folders": {}}
    for top, subs in FOLDERS.items():
        tid = ensure_folder(top, root)
        ids["folders"][top] = tid
        for sub in subs:
            sid = ensure_folder(sub, tid)
            ids["folders"][f"{top}/{sub}"] = sid
    print(f"  folder tree under {root}")
    return ids


# --------------------------------------------------------------------------- #
# collaborations
# --------------------------------------------------------------------------- #
def collab_group(folder_id: str, group_id: str, role: str = "viewer"):
    requests.post(f"{API}/collaborations", headers=hdr(),
                  json={"item": {"id": folder_id, "type": "folder"},
                        "accessible_by": {"id": group_id, "type": "group"},
                        "role": role}, timeout=30)


def collab_user(folder_id: str, user_id: str, role: str = "viewer"):
    """Grant (or UPDATE) a user's collaboration on a folder at `role`.
    Idempotent and role-correcting: if the collaboration already exists at a
    different role (e.g. a prior run granted editor and we now want the tighter
    uploader), update it rather than leaving the looser grant in place."""
    r = requests.post(f"{API}/collaborations", headers=hdr(),
                      json={"item": {"id": folder_id, "type": "folder"},
                            "accessible_by": {"id": user_id, "type": "user"},
                            "role": role}, timeout=30)
    if r.status_code < 300:
        return
    # Box returns 400 (or 409) when the collaboration already exists — in either
    # case, reconcile the role to the requested one via PUT (so a downgrade from
    # a prior run's looser grant actually lands).
    cols = requests.get(f"{API}/folders/{folder_id}/collaborations",
                        headers=hdr(), timeout=30).json().get("entries", [])
    for c in cols:
        if (c.get("accessible_by") or {}).get("id") == str(user_id):
            if c.get("role") != role:
                requests.put(f"{API}/collaborations/{c['id']}", headers=hdr(),
                             json={"role": role}, timeout=30)
            return


def apply_permissions(ids: dict, group_id: str, users: dict[str, str]):
    collab_group(ids["folders"]["Sources"], group_id, "viewer")
    collab_group(ids["folders"]["Memory"], group_id, "viewer")
    # Restricted: only a selected few.
    #   - gateway vendor review (Oct 14): Dana + Priya   -> FAILURE scenario for Alex
    #   - checkout budget review (Oct 5): Dana + Jordan   -> PARTIAL scenario for Alex
    # Collaboration is at the folder level, so the selected set on Restricted is
    # the union: Dana, Priya, Jordan. Alex/Sam/Nina are NOT on Restricted.
    collab_user(ids["folders"]["Restricted"], users["Dana Kim"], "viewer")
    collab_user(ids["folders"]["Restricted"], users["Priya Nair"], "viewer")
    collab_user(ids["folders"]["Restricted"], users["Jordan Blake"], "viewer")
    # Quarantine: no collaborators
    print("  permissions applied (Sources/Memory -> group; "
          "Restricted -> Dana, Priya, Jordan)")


def apply_agent_permissions(ids: dict, agents: dict[str, str]):
    """Grant each agent ONLY its role's folders: editor on its write set, viewer
    on its read-only set. This is the least-privilege boundary Box enforces — a
    test that tells the Scribe to write a decision gets a permission error."""
    for name, scope in AGENT_USERS.items():
        uid = agents.get(name)
        if not uid:
            continue
        for path in scope.get("write", []):
            fid = ids["folders"].get(path)
            if fid:
                collab_user(fid, uid, "editor")
        for path in scope.get("upload", []):
            fid = ids["folders"].get(path)
            if fid:
                collab_user(fid, uid, "uploader")   # drop-only, cannot read contents
        granted = set(scope.get("write", [])) | set(scope.get("upload", []))
        for path in scope.get("read", []):
            if path in granted:   # don't downgrade a folder already granted higher
                continue
            fid = ids["folders"].get(path)
            if fid:
                collab_user(fid, uid, "viewer")
        print(f"  agent {name}: write={scope.get('write')} "
              f"upload={scope.get('upload', [])} read={scope.get('read')}")


# --------------------------------------------------------------------------- #
# uploads + metadata
# --------------------------------------------------------------------------- #
def upload_area_files(ids: dict) -> dict[str, str]:
    """Upload Sources/* + Restricted/* + Quarantine/* and Memory/*. Return path->file_id."""
    file_ids: dict[str, str] = {}

    def do_upload(local: Path, folder_id: str):
        fid = find_or_upload(folder_id, local)
        file_ids[str(local.relative_to(DATA))] = fid
        meta = local.parent / (local.name + ".meta.json")
        if meta.exists():
            apply_meta(fid, json.loads(meta.read_text()))
        return fid

    # Sources areas + Restricted + Quarantine
    for area, path_parts in AREA_TO_PATH.items():
        folder_key = "/".join(path_parts)
        folder_id = ids["folders"][folder_key]
        src_dir = DATA / (area if area in ("Restricted", "Quarantine") else area)
        # Sources files live at DATA/<area>; Restricted/Quarantine at DATA/<area>
        src_dir = DATA / area
        if not src_dir.exists():
            continue
        for f in sorted(src_dir.glob("*")):
            if f.name.endswith(".meta.json") or f.is_dir():
                continue
            do_upload(f, folder_id)

    # Memory subfolders
    mem = DATA / "Memory"
    for sub in ["Meeting Notes", "Decisions", "People", "Handbook"]:
        folder_id = ids["folders"][f"Memory/{sub}"]
        for f in sorted((mem / sub).glob("*")):
            if f.name.endswith(".meta.json") or f.is_dir():
                continue
            do_upload(f, folder_id)

    return file_ids


_FOLDER_CACHE: dict[str, dict[str, str]] = {}


def find_or_upload(folder_id: str, local: Path) -> str:
    # list each folder ONCE (cached name->id) — avoids O(files) redundant
    # listings when uploading hundreds of bulk files.
    cache = _FOLDER_CACHE.get(folder_id)
    if cache is None:
        cache = {it["name"]: it["id"] for it in T.folder_items(folder_id)
                 if it["type"] == "file"}
        _FOLDER_CACHE[folder_id] = cache
    if local.name in cache:
        return cache[local.name]
    fid = T.upload_file(folder_id, str(local))
    cache[local.name] = fid
    return fid


def apply_meta(file_id: str, meta: dict):
    template = meta.pop("_template", "shadowItem")
    # drop empty-string keys Box would reject for date/enum? keep strings as-is
    clean = {k: v for k, v in meta.items() if v not in ("", None, [])}
    if not clean:
        return
    T.apply_metadata(file_id, template, clean)


def link_decision_sources(file_ids: dict[str, str]):
    """Fill shadowDecision.sourceFileIds with the Mar 12 standup + design doc ids."""
    dec = file_ids.get("Memory/Decisions/retry-idempotency-keys.md")
    mar = file_ids.get("Meetings/2026-03-12-payments-standup.txt")
    doc = file_ids.get("Docs/retry-idempotency-design.md")
    if dec and mar and doc:
        T.apply_metadata(dec, "shadowDecision",
                         {"sourceFileIds": f"{mar},{doc}"})
        print(f"  linked decision sources -> {mar},{doc}")


def quarantine_activity():
    """Write the 'Quarantined 1 item (HR)' activity row via the backend DB helper."""
    try:
        from web.activity import log_activity  # optional; backend may add later
        log_activity("Librarian", "Quarantined 1 item (HR) - never used in answers", "")
    except Exception:
        pass


# --------------------------------------------------------------------------- #
def main():
    print("Shadow Box setup")
    me = get_service_client().users.get_user_me()
    print(f"service account: {me.name} ({me.id})")

    print("templates:")
    templates = ensure_templates()
    print("app users:")
    users = ensure_app_users()
    print("agent users:")
    agents = ensure_agent_users()
    print("group:")
    group_id = ensure_group(list(users.values()))
    print("folders:")
    ids = build_tree()
    print("permissions:")
    apply_permissions(ids, group_id, users)
    apply_agent_permissions(ids, agents)
    print("uploads + metadata:")
    file_ids = upload_area_files(ids)
    link_decision_sources(file_ids)

    out = {
        "enterprise_id": os.environ.get("BOX_ENTERPRISE_ID", ""),
        "service_account_id": me.id,
        "root_folder_id": ids["root"],
        "folders": ids["folders"],
        "users": users,
        "agent_users": agents,
        "group_id": group_id,
        "templates": templates,
        "file_ids": file_ids,
        "components": COMPONENTS,
    }
    IDS_PATH.write_text(json.dumps(out, indent=2))
    print(f"\nwrote {IDS_PATH}")
    print(f"  root={ids['root']}  users={len(users)}  files={len(file_ids)}")
    print("set SHADOW_ROOT_FOLDER_ID in .env to:", ids["root"])


if __name__ == "__main__":
    main()
