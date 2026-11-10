"""Synthetic parameter/telemetry fixtures and loopback server; no real device data."""

import asyncio
import copy
from dataclasses import dataclass

from aiohttp import web

CHALLENGE = "0" * 64
KEY = (
    "a7cb08a85c66c00f574c4379c2cae8b2c5fe225ea87a4b9780edce4461282185"
    "aebd83a18eaeea52463076a3dd1bc0e19c9dc3d9d895c5debe3b2f480aeffd49"
)
AUTH_COOKIE = "a" * 64
REFRESH_COOKIE = "b" * 64


def numeric(name="4", type_name="FLOAT", value=20.5, **changes):
    row = {
        "name": name, "type": type_name, "securityLevel": "USER", "active": True,
        "unit": "°C", "minimum": 0, "maximum": 30, "increment": 0.5,
        "displayName": "Test menu", "description": "Synthetic parameter", "value": value,
    }
    row.update(changes)
    return row


def user_catalog():
    rows = []
    for name in ("4", "5", "7", "8", "HP4", "HP5"):
        rows.append(numeric(name, active=not name.startswith("HP")))
    for name in ("2", "HP2", "11", "12", "13", "Setup1a", "Setup2", "Setup3",
                 "mode.remote_control.lock_duration", "fws.auslaufSoll"):
        rows.append(numeric(name, "INTEGER", 10, minimum=-20, maximum=75, increment=1))
    daily = [{"start": "00:00:00", "end": "24:00:00"},
             {"start": "00:00:00", "end": "00:00:00"}]
    weekly = [{"weekdays": [1, 2, 3, 4, 5, 6, 7], "time_ranges": daily},
              {"weekdays": [], "time_ranges": daily[1:]}]
    for type_name, names, value in (
        ("DAILY_CLOCK_RANGE", ("1", "3", "6", "HP1", "HP3", "boiler1.zirk.freigabe",
                               "boilerA.zirk.freigabe", "fws.freigabe.tagesuhr",
                               "fws.freigabe.tagesuhr.zirk"), daily),
        ("WEEKLY_CLOCK_RANGE", ("1a", "3a", "6a", "HP1a", "HP3a",
                                "fws.freigabe.wochenuhr", "fws.freigabe.wochenuhr.zirk",
                                "kessel.fremdwaerme.freigabezeit", "kombi.freigabezeit"), weekly),
        ("BOOLEAN", ("Setup1", "Setup5", "mode.remote_control.enabled", "E18"), False),
        ("TEXT", ("Setup4a",), ""),
        ("DATETIME", ("Setup6", "Z91"),
         {"year": 0, "month": 0, "day": 0, "hours": -1, "minutes": -1, "seconds": -1}),
    ):
        for name in names:
            rows.append({"name": name, "type": type_name, "value": copy.deepcopy(value),
                         "securityLevel": "USER", "active": True})
    choices = [{"id": 0, "name": "First"}, {"id": 1, "name": "Second"}]
    for name in ("Setup4", "Setup7", "Setup8"):
        rows.append({"name": name, "type": "MULTIPLE_CHOICE" if name == "Setup8" else "CHOICE",
                     "choices": choices, "value": choices if name == "Setup8" else choices[0],
                     "securityLevel": "USER", "active": True})
    assert len(rows) == 44
    return {row["name"]: row for row in rows}


@dataclass
class Reply:
    status: int = 200
    document: object = None
    raw: bytes | None = None
    headers: dict | None = None
    reason: str | None = None

    def response(self):
        if self.raw is not None:
            return web.Response(status=self.status, body=self.raw, headers=self.headers,
                                reason=self.reason)
        return web.json_response(self.document, status=self.status, headers=self.headers,
                                 reason=self.reason)


class Device:
    def __init__(self):
        self.catalog = user_catalog()
        for name, level in (("installer", "PLUMBER"), ("service", "SERVICE"),
                            ("developer", "DEVELOPER")):
            self.catalog[name] = numeric(name, securityLevel=level)
        self.requests = []
        self.challenge_statuses = []
        self.auth_statuses = []
        self.challenge_document = {"challenge": CHALLENGE}
        self.issue_cookie = True
        self.expected_cookie = AUTH_COOKIE
        self.read_replies = []
        self.write_reply = None
        self.silent_rejection = False
        self.disconnect_after_write = False
        self.write_delay = 0
        self.block_write = False
        self.write_started = asyncio.Event()
        self.release_write = asyncio.Event()
        self.require_cookie = True
        self.channels = {"example.temperature": 42.5, "example.state": 0}
        self.info = {
            "softwareVersion": "test_firmware_1.0", "mac": "02:00:00:00:00:01",
            "systemId": "test-system-id", "commissionNumber": "test-commission",
            "articleSerialNumber": "test-serial",
        }
        self.pcb = {"internalTemperature": 26.5, "internalVoltage": 23.8}
        self.snapshot_replies = {path: [] for path in ("/channels", "/info", "/pcb")}

    def posts(self):
        return [r for r in self.requests if (r["method"], r["path"]) == ("POST", "/parameters")]

    def auths(self):
        return [r for r in self.requests if r["path"] == "/authenticate"]

    async def handle(self, request):
        body = await request.json() if request.can_read_body else None
        self.requests.append({"method": request.method, "path": request.path,
                              "body": body, "cookie": request.cookies.get("auth-cookie")})
        if request.path == "/challenge" and request.method == "GET":
            status = self.challenge_statuses.pop(0) if self.challenge_statuses else 200
            return web.json_response(self.challenge_document, status=status)
        if request.path == "/authenticate" and request.method == "POST":
            assert body == {"key": KEY, "daqOnly": True}, "unsafe/incorrect authentication"
            assert body["daqOnly"] is True, "daqOnly must be a JSON boolean, not numeric 1"
            status = self.auth_statuses.pop(0) if self.auth_statuses else 204
            self.expected_cookie = AUTH_COOKIE
            response = web.Response(status=status)
            if self.issue_cookie and status in (200, 204):
                response.set_cookie("auth-cookie", AUTH_COOKIE, path="/")
            return response
        if request.path not in {"/parameters", "/channels", "/info", "/pcb"}:
            raise AssertionError(f"forbidden endpoint {request.path}")
        if self.require_cookie and request.cookies.get("auth-cookie") != self.expected_cookie:
            return web.Response(status=401)
        if request.path in self.snapshot_replies:
            assert request.method == "GET" and body is None, "snapshot routes are GET-only"
            replies = self.snapshot_replies[request.path]
            if replies:
                return replies.pop(0).response()
            document = {"/channels": self.channels, "/info": self.info, "/pcb": self.pcb}[request.path]
            response = web.json_response(document)
            self.expected_cookie = REFRESH_COOKIE
            response.set_cookie("auth-cookie", REFRESH_COOKIE, path="/")
            return response
        if request.method == "GET":
            if self.read_replies:
                return self.read_replies.pop(0).response()
            names = list(self.catalog) if body is None else body["parameters"]
            rows = [self.catalog[name] for name in names if name in self.catalog]
            response = web.json_response({"parameters": rows})
            self.expected_cookie = REFRESH_COOKIE
            response.set_cookie("auth-cookie", REFRESH_COOKIE, path="/")
            return response
        assert request.method == "POST", "unexpected parameter method"
        assert isinstance(body, dict) and set(body) == {"parameters"}
        assert len(body["parameters"]) == 1
        entry = body["parameters"][0]
        assert set(entry) == {"name", "value"}
        name = entry["name"]
        if not self.silent_rejection:
            self.catalog[name]["value"] = entry["value"]
        self.write_started.set()
        if self.block_write:
            await self.release_write.wait()
        if self.write_delay:
            await asyncio.sleep(self.write_delay)
        if self.disconnect_after_write:
            request.transport.close()
        if self.write_reply is not None:
            return self.write_reply.response()
        return web.json_response({"parameters": [self.catalog[name]]})
