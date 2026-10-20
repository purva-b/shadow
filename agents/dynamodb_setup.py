"""Idempotently create the DynamoDB `shadow_activity` table (activity feed).

    python agents/dynamodb_setup.py

Safe to import without running. Does nothing if the table already exists or if
AWS credentials are unavailable.
"""
from __future__ import annotations

import os

TABLE = "shadow_activity"


def ensure_table() -> str:
    import boto3
    from botocore.exceptions import ClientError

    region = os.environ.get("AWS_REGION", "us-east-1")
    ddb = boto3.client("dynamodb", region_name=region)
    try:
        ddb.describe_table(TableName=TABLE)
        return "exists"
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise
    ddb.create_table(
        TableName=TABLE,
        AttributeDefinitions=[{"AttributeName": "ts", "AttributeType": "S"}],
        KeySchema=[{"AttributeName": "ts", "KeyType": "HASH"}],
        BillingMode="PAY_PER_REQUEST",
    )
    ddb.get_waiter("table_exists").wait(TableName=TABLE)
    return "created"


if __name__ == "__main__":
    try:
        from pathlib import Path

        from dotenv import load_dotenv

        load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    except Exception:
        pass
    print(ensure_table())
