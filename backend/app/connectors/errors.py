"""Translate raw driver errors into hints a user can act on."""

from __future__ import annotations

import re

_HINTS: list[tuple[re.Pattern, str]] = [
    (
        re.compile(r"not installed|No module named", re.I),
        "The database driver is missing on the server. Use the Docker image (it ships every driver) or install the "
        "package named in the message.",
    ),
    (
        re.compile(r"ORA-12514|DPY-6001|DPY-4027|service name", re.I),
        "The Oracle listener does not know this service name. Use the service name (for example ORCLPDB1), not the "
        "SID, and check it with your DBA.",
    ),
    (
        re.compile(
            r"ORA-01017|invalid username/password|password authentication failed|Incorrect username or password|"
            r"Authentication failed|Login failed|Failed to authenticate|invalid access token|Invalid token|"
            r"401|403 Forbidden|access denied",
            re.I,
        ),
        "Sign-in was rejected. Check the username and password (or token), that the account is not locked, and that "
        "it is allowed to connect from this application.",
    ),
    (
        re.compile(r"250001|Could not connect to Snowflake|account .* not found|Incorrect account", re.I),
        "Check the Snowflake account identifier. Use the part of your URL before .snowflakecomputing.com, e.g. "
        "'myorg-myaccount' or 'xy12345.eu-west-1'.",
    ),
    (
        re.compile(
            r"could not translate host name|Name or service not known|failed to lookup address|getaddrinfo|"
            r"nodename nor servname|Unknown host|No such host|DPY-6005.*resolv",
            re.I,
        ),
        "The host name could not be found. Check the spelling of the host / server name and that it is reachable "
        "from where this application runs (VPN or private DNS).",
    ),
    (
        re.compile(
            r"timed out|timeout|Connection refused|No route to host|ORA-12170|ORA-12541|DPY-6005|"
            r"Network is unreachable|connection reset",
            re.I,
        ),
        "The server could not be reached. Check the host and port, that the database is running, and that firewalls "
        "or security groups allow connections from this application's IP address.",
    ),
    (
        re.compile(
            r"ORA-00942|does not exist|not found|TABLE_OR_VIEW_NOT_FOUND|Table or view not found|"
            r"Invalid object name|No files found|Folder not found|File not found",
            re.I,
        ),
        "The table, schema or path was not found. Check the name (including schema/catalog and upper/lower case) and "
        "that this user has permission to see it.",
    ),
    (
        re.compile(r"permission denied|insufficient privileges|ORA-01031|not authorized", re.I),
        "The user can connect but lacks permission. Grant SELECT on the tables (and USAGE on the schema/warehouse).",
    ),
    (
        re.compile(r"warehouse.*(suspended|does not exist|not.*exist)|No active warehouse", re.I),
        "The Snowflake warehouse is missing or suspended. Check the warehouse name and that the role can use it.",
    ),
    (
        re.compile(r"http_path|SQL warehouse|cluster.*(terminated|not running)", re.I),
        "Check the Databricks HTTP path (SQL warehouse → Connection details) and that the warehouse or cluster is "
        "running.",
    ),
    (
        re.compile(r"Parser Error|syntax error|ORA-00933|ORA-00904|Binder Error|invalid identifier", re.I),
        "A filter, expression or column name is not valid SQL for this engine. Check the filters and custom rules of "
        "this table.",
    ),
    (
        re.compile(r"inside the server data folder", re.I),
        "Local files must be placed in the server's data folder (the /data volume in Docker). Use a relative path, "
        "or a cloud path such as s3://bucket/folder.",
    ),
]


def explain(message: str) -> str | None:
    for pattern, hint in _HINTS:
        if pattern.search(message or ""):
            return hint
    return None


def connector_http_error(exc: Exception):
    """HTTP 400 whose message includes the actionable hint."""
    from fastapi import HTTPException

    message = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
    hint = explain(message)
    return HTTPException(400, f"{message}. {hint}" if hint else message)
