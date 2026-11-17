"""IDFMCP-190 approval decision Lambda.

Route: POST /approvals/{approval_id}/decision   (API Gateway, IAM auth;
       called by the portal backend on behalf of the tool owner)

Request body:  {"decision": "approve" | "deny", "reason": "optional text"}
Header:        X-Approver-Id: <KUMS id of the owner, set by the portal>

Approvals item (written by the Step Functions workflow):
  approvalId (PK), instanceId, ownerId, status ("pending" | "approved" |
  "denied"), taskToken, createdAt, expiresAt (TTL)

Environment: APPROVALS_TABLE
"""

import json
import logging
import os
import time

import boto3
from botocore.exceptions import BotoCoreError, ClientError

LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(logging.INFO)

APPROVALS_TABLE = os.environ["APPROVALS_TABLE"]
_ddb = boto3.client("dynamodb")
_sfn = boto3.client("stepfunctions")


class RequestError(Exception):
    def __init__(self, status: int, description: str):
        super().__init__(description)
        self.status = status
        self.description = description


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _parse(event: dict) -> tuple[str, str, str, str]:
    approval_id = (event.get("pathParameters") or {}).get("approval_id")
    if not approval_id:
        raise RequestError(400, "approval_id is required")

    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    approver = headers.get("x-approver-id", "").strip()
    if not approver:
        raise RequestError(400, "X-Approver-Id header is required")

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        raise RequestError(400, "Request body must contain valid JSON") from None
    decision = body.get("decision")
    if decision not in ("approve", "deny"):
        raise RequestError(400, "decision must be 'approve' or 'deny'")
    reason = str(body.get("reason", ""))[:500]
    return approval_id, approver, decision, reason


def _decide(approval_id: str, approver: str, decision: str, reason: str) -> dict:
    item = _ddb.get_item(
        TableName=APPROVALS_TABLE,
        Key={"approvalId": {"S": approval_id}},
        ConsistentRead=True,
    ).get("Item")
    if not item:
        raise RequestError(404, "Approval not found")
    if item.get("ownerId", {}).get("S") != approver:
        raise RequestError(403, "Only the plan owner can decide this approval")

    new_status = "approved" if decision == "approve" else "denied"
    now = int(time.time())
    # Only a pending, not yet expired approval can be decided, and only once.
    try:
        _ddb.update_item(
            TableName=APPROVALS_TABLE,
            Key={"approvalId": {"S": approval_id}},
            UpdateExpression="SET #s = :new, decidedBy = :by, decidedAt = :at, reason = :reason",
            ConditionExpression="#s = :pending AND expiresAt > :now",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":new": {"S": new_status},
                ":pending": {"S": "pending"},
                ":by": {"S": approver},
                ":at": {"N": str(now)},
                ":reason": {"S": reason},
                ":now": {"N": str(now)},
            },
        )
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise RequestError(409, "Approval is already decided or has expired") from None
        raise

    token = item["taskToken"]["S"]
    try:
        if decision == "approve":
            _sfn.send_task_success(
                taskToken=token,
                output=json.dumps({"decision": "approved", "approver": approver}),
            )
        else:
            _sfn.send_task_failure(taskToken=token, error="ApprovalDenied", cause=reason or "Denied by owner")
    except ClientError as error:
        if error.response["Error"]["Code"] in ("TaskTimedOut", "TaskDoesNotExist", "InvalidToken"):
            raise RequestError(410, "Approval workflow is no longer waiting") from None
        raise

    LOGGER.info("Approval %s %s by %s", approval_id, new_status, approver)
    return {"approval_id": approval_id, "status": new_status}


def handle(event: dict, _context: object = None) -> dict:
    try:
        approval_id, approver, decision, reason = _parse(event)
        return _response(200, _decide(approval_id, approver, decision, reason))
    except RequestError as error:
        LOGGER.warning("Decision rejected: status=%s reason=%s", error.status, error.description)
        return _response(error.status, {"description": error.description})
    except (ClientError, BotoCoreError):
        LOGGER.exception("AWS call failed")
        return _response(503, {"description": "Approval service unavailable; retry the request"})
    except Exception:
        LOGGER.exception("Unhandled decision error")
        return _response(500, {"description": "Internal error"})


handler = handle
