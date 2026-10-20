"""Box operations used by Shadow's agents, setup, and backend.

Thin, well-named wrappers over box-sdk-gen plus a few raw REST calls for
endpoints the SDK models awkwardly (AI, metadata queries). Every function
that participates in an *answer* accepts as_user so the call is made with the
asker's user-subject token and Box permissions decide visibility.
"""
from __future__ import annotations

import os
from typing import Any

import requests

from .client import get_client, get_service_client

API = "https://api.box.com/2.0"

# One connection-pooled Session for all raw REST calls, instead of a fresh TCP
# connection per request. Box is one host, so keep-alive + pooling is a real
# win on an answer that makes many reads.
_SESSION = requests.Session()


# --------------------------------------------------------------------------- #
# token helper for raw REST (AI / metadata query endpoints)
# --------------------------------------------------------------------------- #
def _token(as_user: str | None = None) -> str:
    client = get_client(as_user)
    # box-sdk-gen exposes the retrieved token via the auth object
    auth = client.auth
    tok = auth.retrieve_token()
    if not tok.access_token:
        raise RuntimeError("Box auth returned no access token")
    return tok.access_token


def _headers(as_user: str | None = None) -> dict:
    return {
        "Authorization": f"Bearer {_token(as_user)}",
        "Content-Type": "application/json",
    }


# --------------------------------------------------------------------------- #
# clients
# --------------------------------------------------------------------------- #
def get_box_client(user_id: str | None = None):
    return get_client(user_id)


# --------------------------------------------------------------------------- #
# files & folders
# --------------------------------------------------------------------------- #
def upload_file(folder_id: str, path: str, name: str | None = None) -> str:
    """Upload a local file; return the new file id. Service account."""
    from box_sdk_gen import UploadFileAttributes, UploadFileAttributesParentField

    name = name or os.path.basename(path)
    client = get_service_client()
    with open(path, "rb") as fh:
        uploaded = client.uploads.upload_file(
            UploadFileAttributes(
                name=name,
                parent=UploadFileAttributesParentField(id=folder_id),
            ),
            fh,
        )
    if not uploaded.entries:
        raise RuntimeError("Box upload returned no file entry")
    return uploaded.entries[0].id


def upload_new_version(file_id: str, path: str) -> str:
    """Upload a new version of an existing file; return the file id."""
    from box_sdk_gen import (
        UploadFileVersionAttributes,
    )

    client = get_service_client()
    with open(path, "rb") as fh:
        uploaded = client.uploads.upload_file_version(
            file_id,
            UploadFileVersionAttributes(name=os.path.basename(path)),
            fh,
        )
    if not uploaded.entries:
        raise RuntimeError("Box version upload returned no file entry")
    return uploaded.entries[0].id


def move_file(file_id: str, folder_id: str) -> None:
    from box_sdk_gen import UpdateFileByIdParent

    client = get_service_client()
    client.files.update_file_by_id(file_id, parent=UpdateFileByIdParent(id=folder_id))


def create_folder(name: str, parent_id: str) -> str:
    from box_sdk_gen import CreateFolderParent

    client = get_service_client()
    folder = client.folders.create_folder(name, CreateFolderParent(id=parent_id))
    return folder.id


def folder_items(folder_id: str, as_user: str | None = None,
                 md_template: str | None = None) -> list[dict]:
    fields = "id,name,type"
    if md_template:
        eid = os.environ.get("BOX_ENTERPRISE_ID", "")
        scope = f"enterprise_{eid}" if eid else "enterprise"
        fields += f",metadata.{scope}.{md_template}"
    url = f"{API}/folders/{folder_id}/items?fields={fields}&limit=1000"
    r = _SESSION.get(url, headers=_headers(as_user), timeout=30)
    r.raise_for_status()
    return r.json().get("entries", [])


def folder_item_count(folder_id: str, as_user: str | None = None) -> int:
    url = f"{API}/folders/{folder_id}/items?fields=id&limit=1"
    r = _SESSION.get(url, headers=_headers(as_user), timeout=30)
    if r.status_code != 200:
        return 0
    return r.json().get("total_count", 0)


def can_read(file_id: str, as_user: str) -> bool:
    """True iff the given App User can GET this file (permission probe)."""
    url = f"{API}/files/{file_id}?fields=id,name"
    r = _SESSION.get(url, headers=_headers(as_user), timeout=30)
    return r.status_code == 200


# --------------------------------------------------------------------------- #
# metadata
# --------------------------------------------------------------------------- #
_DATE_FIELDS = {"occurredOn", "decidedOn", "lastActiveOn"}


def _normalize_dates(values: dict) -> dict:
    out = dict(values)
    for k in _DATE_FIELDS:
        v = out.get(k)
        if isinstance(v, str) and len(v) == 10 and v[4] == "-":
            out[k] = f"{v}T00:00:00Z"  # Box date fields need RFC3339
    return out


def _patch_ops(values: dict, existing: dict) -> list[dict]:
    """Build JSON-Patch ops choosing 'replace' for a field already present and
    'add' for a new one. Pure + tested — this is the logic that was buggy."""
    return [
        {"op": "replace" if k in existing else "add", "path": f"/{k}", "value": v}
        for k, v in values.items()
    ]


def apply_metadata(file_id: str, template: str, values: dict, scope: str = "enterprise",
                   as_user: str | None = None) -> None:
    """Create the metadata instance, or update it if it already exists.

    On update (409), Box's JSON-Patch requires "replace" for a field that
    already exists and "add" for a new one. The old code tried all-add, then on
    failure all-replace — which breaks for a MIX of new + existing fields (the
    common case): all-add fails on the existing ones, all-replace fails on the
    new ones. Fix: read the current instance and choose the op PER FIELD."""
    values = _normalize_dates(values)
    base = f"{API}/files/{file_id}/metadata/{scope}/{template}"
    r = _SESSION.post(base, headers=_headers(as_user), json=values, timeout=30)
    if r.status_code != 409:
        r.raise_for_status()
        return
    # already exists -> build per-field add/replace from what's currently set
    existing = get_metadata(file_id, template, scope=scope, as_user=as_user) or {}
    ops = _patch_ops(values, existing)
    hdr = _headers(as_user)
    hdr["Content-Type"] = "application/json-patch+json"
    _SESSION.put(base, headers=hdr, json=ops, timeout=30).raise_for_status()


def get_metadata(file_id: str, template: str, as_user: str | None = None,
                 scope: str = "enterprise") -> dict | None:
    url = f"{API}/files/{file_id}/metadata/{scope}/{template}"
    r = _SESSION.get(url, headers=_headers(as_user), timeout=30)
    if r.status_code == 200:
        return r.json()
    return None


def _scope_prefix(scope: str) -> str:
    """Box metadata queries need the full scope, e.g. enterprise_1559584636."""
    if scope == "enterprise":
        eid = os.environ.get("BOX_ENTERPRISE_ID", "")
        return f"enterprise_{eid}" if eid else "enterprise"
    return scope


def metadata_query(template: str, query: str, params: dict, ancestor_folder_id: str,
                   as_user: str | None = None, scope: str = "enterprise",
                   fields: list[str] | None = None, limit: int = 50) -> list[dict]:
    body: dict[str, Any] = {
        "from": f"{_scope_prefix(scope)}.{template}",
        "ancestor_folder_id": ancestor_folder_id,
        "query": query,
        "query_params": params,
        "limit": limit,
    }
    if fields:
        body["fields"] = fields
    r = _SESSION.post(f"{API}/metadata_queries/execute_read",
                      headers=_headers(as_user), json=body, timeout=30)
    if r.status_code >= 400:
        return []
    return r.json().get("entries", [])


# --------------------------------------------------------------------------- #
# search
# --------------------------------------------------------------------------- #
def search(query: str, ancestor_folder_id: str | None = None,
           as_user: str | None = None, limit: int = 10) -> list[dict]:
    params = {"query": query, "limit": limit, "type": "file",
              "fields": "id,name,type"}
    if ancestor_folder_id:
        params["ancestor_folder_ids"] = ancestor_folder_id
    r = _SESSION.get(f"{API}/search", headers=_headers(as_user),
                     params=params, timeout=30)
    if r.status_code >= 400:
        return []
    return r.json().get("entries", [])


# --------------------------------------------------------------------------- #
# Box AI
# --------------------------------------------------------------------------- #
def extract_structured(file_id: str, template: str, scope: str = "enterprise") -> dict:
    """Box AI structured extraction against a metadata template (1 AI unit)."""
    body = {
        "items": [{"id": file_id, "type": "file"}],
        "metadata_template": {"template_key": template, "scope": scope,
                              "type": "metadata_template"},
    }
    r = _SESSION.post(f"{API}/ai/extract_structured", headers=_headers(),
                      json=body, timeout=120)
    r.raise_for_status()
    return r.json()


def ai_ask(file_ids: list[str], question: str, as_user: str | None = None) -> dict:
    """Box AI multi-item Ask with citations, as the asker (1 AI unit)."""
    items = [{"id": fid, "type": "file"} for fid in file_ids[:5]]
    body = {
        "mode": "multiple_item_qa" if len(items) > 1 else "single_item_qa",
        "prompt": question,
        "items": items,
        "include_citations": True,
    }
    r = _SESSION.post(f"{API}/ai/ask", headers=_headers(as_user), json=body, timeout=120)
    if r.status_code >= 400:
        return {"answer": "", "citations": [], "error": r.text, "status": r.status_code}
    return r.json()


# --------------------------------------------------------------------------- #
# tasks
# --------------------------------------------------------------------------- #
def create_task_and_assign(file_id: str, message: str, assignee_id: str) -> dict:
    task = _SESSION.post(
        f"{API}/tasks", headers=_headers(),
        json={"item": {"id": file_id, "type": "file"}, "action": "complete",
              "message": message},
        timeout=30,
    )
    task.raise_for_status()
    task_id = task.json()["id"]
    assign = _SESSION.post(
        f"{API}/task_assignments", headers=_headers(),
        json={"task": {"id": task_id, "type": "task"},
              "assign_to": {"id": assignee_id}},
        timeout=30,
    )
    assign.raise_for_status()
    return {"task_id": task_id, "assignment_id": assign.json()["id"]}


def complete_task(task_assignment_id: str, as_user: str | None = None) -> None:
    # Box only lets the ASSIGNEE resolve a task assignment, so pass the
    # assignee's user id as as_user (the 'Simulate Dana confirming' path).
    r = _SESSION.put(
        f"{API}/task_assignments/{task_assignment_id}",
        headers=_headers(as_user),
        json={"resolution_state": "completed"},
        timeout=30,
    )
    r.raise_for_status()


def list_task_assignments(file_id: str) -> list[dict]:
    tasks = _SESSION.get(f"{API}/files/{file_id}/tasks", headers=_headers(), timeout=30)
    out = []
    for t in tasks.json().get("entries", []):
        a = _SESSION.get(f"{API}/tasks/{t['id']}/assignments",
                         headers=_headers(), timeout=30)
        out.extend(a.json().get("entries", []))
    return out


# --------------------------------------------------------------------------- #
# links
# --------------------------------------------------------------------------- #
def embed_link(file_id: str, as_user: str | None = None) -> str | None:
    url = f"{API}/files/{file_id}?fields=expiring_embed_link"
    r = _SESSION.get(url, headers=_headers(as_user), timeout=30)
    if r.status_code != 200:
        return None
    return (r.json().get("expiring_embed_link") or {}).get("url")


def box_web_url(file_id: str) -> str:
    return f"https://app.box.com/file/{file_id}"
