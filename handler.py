"""IDFMCP-190 changes for lambda/handler.py.

Apply the numbered sections below to your existing handler.py.
Everything not mentioned here stays as it is.
"""

# ---------------------------------------------------------------------------
# 1. IMPORTS (top of handler.py)
#    Add these two lines next to the existing imports.
# ---------------------------------------------------------------------------
import time
from boto3.dynamodb.types import TypeDeserializer


# ---------------------------------------------------------------------------
# 2. CONSTANTS (below INSTANCE_ROUTE / the other module constants)
# ---------------------------------------------------------------------------
TARGETS_TABLE = os.environ["TARGETS_TABLE"]
CATALOG_CACHE_SECONDS = int(os.environ.get("CATALOG_CACHE_SECONDS", "60"))
_DESERIALIZER = TypeDeserializer()
_CATALOG_CACHE: dict[str, object] = {"at": 0.0, "value": None}


# ---------------------------------------------------------------------------
# 3. CATALOG FROM THE TARGETS TABLE
#    Delete the old code that reads catalog.json (the function that opens
#    Path(...)/"catalog.json", usually decorated with @cache) and delete the
#    catalog.json file from the Lambda folder. Add these functions instead.
#
#    Expected targets item (one row per plan):
#      plan_id (PK), service_id, service_name, service_description,
#      plan_name, plan_description, owner_id, owner_email,
#      approval_required (bool), bindable (bool), free (bool),
#      status ("active" | "inactive")
# ---------------------------------------------------------------------------
@cache
def _targets_client():
    return boto3.client("dynamodb")


def _plain(item: dict) -> dict:
    """DynamoDB attribute map -> plain Python dict."""
    return {key: _DESERIALIZER.deserialize(value) for key, value in item.items()}


def _load_targets() -> list[dict]:
    items: list[dict] = []
    kwargs: dict[str, object] = {"TableName": TARGETS_TABLE}
    while True:
        page = _targets_client().scan(**kwargs)
        items.extend(_plain(item) for item in page.get("Items", []))
        if "LastEvaluatedKey" not in page:
            return items
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def _catalog() -> dict[str, object]:
    """OSB catalog built from active targets, cached for CATALOG_CACHE_SECONDS."""
    now = time.monotonic()
    if _CATALOG_CACHE["value"] is not None and now - float(_CATALOG_CACHE["at"]) < CATALOG_CACHE_SECONDS:
        return _CATALOG_CACHE["value"]

    services: dict[str, dict] = {}
    for target in _load_targets():
        if target.get("status", "active") != "active":
            continue
        service_id = str(target["service_id"])
        service = services.setdefault(service_id, {
            "id": service_id,
            "name": str(target.get("service_name", service_id)),
            "description": str(target.get("service_description", "")),
            "bindable": bool(target.get("bindable", False)),
            "plan_updateable": False,
            "metadata": {"async_required": True},
            "plans": [],
        })
        service["plans"].append({
            "id": str(target["plan_id"]),
            "name": str(target.get("plan_name", target["plan_id"])),
            "description": str(target.get("plan_description", "")),
            "free": bool(target.get("free", True)),
            "metadata": {"approval_required": bool(target.get("approval_required", True))},
        })

    catalog = {"services": [s for s in services.values() if s["plans"]]}
    _CATALOG_CACHE.update(at=now, value=catalog)
    return catalog


def _target(service_id: str, plan_id: str) -> dict:
    """Return the active target for this plan, or reject the request with 400."""
    response = _targets_client().get_item(
        TableName=TARGETS_TABLE,
        Key={"plan_id": {"S": plan_id}},
        ConsistentRead=True,
    )
    item = response.get("Item")
    target = _plain(item) if item else None
    if (
        not target
        or str(target.get("service_id")) != service_id
        or target.get("status", "active") != "active"
    ):
        raise RequestError(400, "Unknown service_id or plan_id")
    return target


# ---------------------------------------------------------------------------
# 4. CATALOG ROUTE (inside handle(), the GET /v2/catalog branch)
#    Replace the line that returns the old static catalog with:
# ---------------------------------------------------------------------------
#        status, body = 200, _catalog()


# ---------------------------------------------------------------------------
# 5. PLAN CHECK IN _provision()
#    Replace the existing check against the static catalog (the one that
#    raises "Unknown service_id or plan_id") with:
# ---------------------------------------------------------------------------
#    target = _target(service_id, plan_id)
#
#    Then, in the instance Item that _provision() writes in the transaction
#    (the row with state="provisioning"), add these attributes so the
#    approval workflow gets them from the instances bus event:
#
#        "approval_required": {"BOOL": bool(target.get("approval_required", True))},
#        "owner_id": {"S": str(target.get("owner_id", ""))},
#        "owner_email": {"S": str(target.get("owner_email", ""))},
#
#    (If your Item is a plain dict serialised with TypeSerializer, add them
#     as plain values instead: "approval_required": bool(...), etc.)


# ---------------------------------------------------------------------------
# 6. UNKNOWN INSTANCE ON last_operation -> 410 (OSB spec), in _last_operation()
#    Change:
#        raise RequestError(404, "Instance not found")
#    to:
#        raise RequestError(410, "Instance not found")
# ---------------------------------------------------------------------------
