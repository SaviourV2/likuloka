"""Typed client for the Floorcraft web app's private HTTP API.

Floorcraft (https://floorcraft.chirostudio.xyz) has no public API. This client
mirrors the calls its browser editor makes to the same-origin ``/api`` backend.
It was written from the editor's JavaScript bundle, not from a recorded browser
session, so read the "Verified" section of the README before relying on it.

Standard library only; Python 3.10+.

    from floorcraft_client import FloorcraftClient

    fc = FloorcraftClient(cookie_file="floorcraft.cookies")
    fc.login("you@example.com", "password")      # once; the session is saved
    for plan in fc.list_plans().plans:
        print(plan.id, plan.name, plan.updated_at)

Deliberately not wrapped: buying coins (``/api/pay/checkout``), spending coins
(``/api/coins/spend``) and the developer grant (``/api/coins/dev-grant``).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from http.cookiejar import Cookie, CookieJar, MozillaCookieJar
from typing import Any, Mapping

BASE_URL = "https://floorcraft.chirostudio.xyz"
USER_AGENT = "floorcraft-api-client/0.1"

Json = dict[str, Any]


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class FloorcraftError(Exception):
    """The server answered with an error, or could not be reached.

    ``code`` is the server's machine-readable ``error`` field, for example
    ``NOT_SIGNED_IN``, ``EMAIL_NOT_VERIFIED``, ``NOT_ENOUGH_COINS``,
    ``CODE_REQUIRED``, ``WRONG_CODE`` or ``RATE_LIMIT``. ``status`` is the HTTP
    status, or 0 when the request never reached the server.
    """

    def __init__(self, status: int, code: str | None, message: str, payload: Json):
        super().__init__(f"[{status}] {code or 'ERROR'}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.payload = payload

    @property
    def retry_in(self) -> int | None:
        """Seconds to wait before retrying, when the server says so (HTTP 409)."""
        value = self.payload.get("retryIn")
        return int(value) if isinstance(value, (int, float)) else None


class NotSignedIn(FloorcraftError):
    """HTTP 401: the call needs a signed-in session."""


class RateLimited(FloorcraftError):
    """HTTP 429: too many attempts."""


# --------------------------------------------------------------------------
# Models
#
# Every model keeps the server's JSON in ``raw``. The named fields are the ones
# the editor reads; anything else the server sends is still reachable there.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class User:
    name: str
    coins: int | None
    avatar_url: str | None
    raw: Json = field(repr=False, default_factory=dict)

    @classmethod
    def from_json(cls, data: Json) -> "User":
        return cls(
            name=str(data.get("name", "")),
            coins=data.get("coins"),
            avatar_url=data.get("avatarUrl"),
            raw=data,
        )


@dataclass(frozen=True)
class Session:
    """Answer of ``GET /api/auth/me``. ``user`` is ``None`` when signed out."""

    user: User | None
    google_enabled: bool
    raw: Json = field(repr=False, default_factory=dict)


@dataclass(frozen=True)
class PlanSummary:
    """One row of the "Your floor plans" list."""

    id: str
    name: str
    thumb: str | None
    updated_at: str | None
    raw: Json = field(repr=False, default_factory=dict)

    @classmethod
    def from_json(cls, data: Json) -> "PlanSummary":
        return cls(
            id=str(data.get("id", "")),
            name=str(data.get("name", "")),
            thumb=data.get("thumb"),
            updated_at=data.get("updatedAt"),
            raw=data,
        )


@dataclass(frozen=True)
class Plan:
    """A floor plan.

    ``data`` is the editor's document as a JSON *string* (``{"v": 2, "plan":
    ..., "gen": ...}``); use :meth:`document` to parse it. It is ``None`` on
    answers that only return metadata, such as rename or the sharing calls.
    The sharing fields are ``None`` when the server leaves them out.
    """

    id: str
    name: str
    data: str | None
    thumb: str | None
    updated_at: str | None
    # 3D walkthrough sharing
    tour_unlocked: bool | None
    tour_token: str | None
    tour_private: bool | None
    tour_code: str | None
    tour_domains: list[str] | None
    # AR sharing and model export
    ar_unlocked: bool | None
    ar_token: str | None
    ar_code: str | None
    glb_unlocked: bool | None
    raw: Json = field(repr=False, default_factory=dict)

    @classmethod
    def from_json(cls, data: Json) -> "Plan":
        return cls(
            id=str(data.get("id", "")),
            name=str(data.get("name", "")),
            data=data.get("data"),
            thumb=data.get("thumb"),
            updated_at=data.get("updatedAt"),
            tour_unlocked=data.get("tourUnlocked"),
            tour_token=data.get("tourToken"),
            tour_private=data.get("tourPrivate"),
            tour_code=data.get("tourCode"),
            tour_domains=data.get("tourDomains"),
            ar_unlocked=data.get("arUnlocked"),
            ar_token=data.get("arToken"),
            ar_code=data.get("arCode"),
            glb_unlocked=data.get("glbUnlocked"),
            raw=data,
        )

    def document(self) -> Json:
        """The plan document parsed from ``data`` (empty when there is none)."""
        return json.loads(self.data) if self.data else {}


@dataclass(frozen=True)
class PlanList:
    plans: list[PlanSummary]
    new_plan_cost: int | None  # coins a new plan costs
    coins: int | None  # the account's balance
    raw: Json = field(repr=False, default_factory=dict)


@dataclass(frozen=True)
class SharedPlan:
    """A plan opened through a public walkthrough or AR link."""

    name: str
    data: str
    raw: Json = field(repr=False, default_factory=dict)

    def document(self) -> Json:
        return json.loads(self.data) if self.data else {}


@dataclass(frozen=True)
class PriceRegion:
    id: str
    label: str
    coin_price: float
    min_coins: int


@dataclass(frozen=True)
class Prices:
    """Coin pricing for the caller's region (``GET /api/pay/prices``)."""

    currency: str
    country: str | None
    region: str
    region_label: str
    coin_price: float
    min_coins: int
    max_coins: int
    bonuses: list[Json]  # [{"fromCoins": 100, "percent": 20}, ...]
    enabled: bool
    regions: list[PriceRegion]
    costs: dict[str, int]  # coins per action: plan, glb, ar, tour, import
    welcome_coins: int | None
    raw: Json = field(repr=False, default_factory=dict)

    @classmethod
    def from_json(cls, data: Json) -> "Prices":
        return cls(
            currency=str(data.get("currency", "")),
            country=data.get("country"),
            region=str(data.get("region", "")),
            region_label=str(data.get("regionLabel", "")),
            coin_price=float(data.get("coinPrice", 0)),
            min_coins=int(data.get("minCoins", 0)),
            max_coins=int(data.get("maxCoins", 0)),
            bonuses=list(data.get("bonuses") or []),
            enabled=bool(data.get("enabled")),
            regions=[
                PriceRegion(
                    id=str(r.get("id", "")),
                    label=str(r.get("label", "")),
                    coin_price=float(r.get("coinPrice", 0)),
                    min_coins=int(r.get("minCoins", 0)),
                )
                for r in data.get("regions") or []
            ],
            costs=dict(data.get("costs") or {}),
            welcome_coins=data.get("welcomeCoins"),
            raw=data,
        )


@dataclass(frozen=True)
class CoinBalance:
    coins: int
    costs: dict[str, int]
    pricing: Json | None
    raw: Json = field(repr=False, default_factory=dict)


@dataclass(frozen=True)
class Purchase:
    id: str
    state: str  # "paid", "pending" or "failed"
    coins: int
    bonus: int | None
    amount: float
    currency: str
    created_at: str | None
    paid_at: str | None
    test: bool
    raw: Json = field(repr=False, default_factory=dict)

    @classmethod
    def from_json(cls, data: Json) -> "Purchase":
        return cls(
            id=str(data.get("id", "")),
            state=str(data.get("state", "")),
            coins=int(data.get("coins", 0)),
            bonus=data.get("bonus"),
            amount=float(data.get("amount", 0)),
            currency=str(data.get("currency", "")),
            created_at=data.get("createdAt"),
            paid_at=data.get("paidAt"),
            test=bool(data.get("test")),
            raw=data,
        )


@dataclass(frozen=True)
class ShareLink:
    """The pieces of a public ``/tour/`` or ``/ar/`` link."""

    kind: str  # "tour" or "ar"
    plan_id: str
    key: str
    code: str


# --------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------


class FloorcraftClient:
    """Calls Floorcraft's ``/api`` the way its editor does.

    Authentication is a session cookie. Three ways to get one:

    * :meth:`login` with an email and password;
    * ``cookie_file``: a path where the session is saved and reloaded, so a
      login survives between runs (keep that file out of version control);
    * ``cookie``: a ``Cookie`` header value copied from a signed-in browser,
      which is the only route for accounts that sign in with Google.
    """

    def __init__(
        self,
        base_url: str = BASE_URL,
        *,
        cookie_file: str | os.PathLike[str] | None = None,
        cookie: str | None = None,
        timeout: float = 30.0,
        user_agent: str = USER_AGENT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.user_agent = user_agent
        self._cookie_file = os.fspath(cookie_file) if cookie_file else None
        self._jar: CookieJar
        if self._cookie_file:
            jar = MozillaCookieJar(self._cookie_file)
            if os.path.exists(self._cookie_file):
                jar.load(ignore_discard=True, ignore_expires=True)
            self._jar = jar
        else:
            self._jar = CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._jar)
        )
        if cookie:
            self.set_cookie_header(cookie)

    # -- transport ---------------------------------------------------------

    def set_cookie_header(self, header: str) -> None:
        """Load cookies from a browser's ``Cookie`` header (``a=1; b=2``)."""
        host = urllib.parse.urlsplit(self.base_url).hostname or ""
        for part in header.split(";"):
            name, sep, value = part.strip().partition("=")
            if not sep or not name:
                continue
            self._jar.set_cookie(
                Cookie(
                    version=0, name=name, value=value, port=None,
                    port_specified=False, domain=host, domain_specified=False,
                    domain_initial_dot=False, path="/", path_specified=True,
                    secure=True, expires=None, discard=False, comment=None,
                    comment_url=None, rest={},
                )
            )
        self._save_cookies()

    def _save_cookies(self) -> None:
        if isinstance(self._jar, MozillaCookieJar):
            self._jar.save(ignore_discard=True, ignore_expires=True)
            os.chmod(self._cookie_file, 0o600)  # type: ignore[arg-type]

    def request(
        self, path: str, body: Mapping[str, Any] | None = None, method: str | None = None
    ) -> Json:
        """Send one call and return the decoded JSON object.

        Follows the editor's own rules: a path that does not start with
        ``/api/`` is an auth path (``/login`` means ``/api/auth/login``), and
        the method is ``GET`` without a body and ``POST`` with one. Raises
        :class:`FloorcraftError` on any non-2xx answer.
        """
        url = self.base_url + (path if path.startswith("/api/") else f"/api/auth{path}")
        headers = {"Accept": "application/json", "User-Agent": self.user_agent}
        payload: bytes | None = None
        if body is not None:
            payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            url, data=payload, headers=headers,
            method=method or ("GET" if body is None else "POST"),
        )
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                status, text = resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as err:
            status, text = err.code, err.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError) as err:
            raise FloorcraftError(0, "OFFLINE", f"Can't reach the server: {err}", {}) from err
        finally:
            self._save_cookies()

        try:
            data = json.loads(text) if text else {}
        except json.JSONDecodeError:
            data = {}
        if not isinstance(data, dict):
            data = {"value": data}

        if 200 <= status < 300:
            return data
        code = data.get("error")
        message = data.get("message") or f"HTTP {status}"
        if status == 401:
            raise NotSignedIn(status, code or "NOT_SIGNED_IN", message, data)
        if status == 429:
            raise RateLimited(status, code or "RATE_LIMIT", message, data)
        raise FloorcraftError(status, code, message, data)

    @staticmethod
    def _plan(data: Json) -> Plan:
        return Plan.from_json(data.get("plan") or {})

    # -- account -----------------------------------------------------------

    def me(self) -> Session:
        """Who is signed in. Works signed out (``user`` is then ``None``)."""
        data = self.request("/me")
        user = data.get("user")
        return Session(
            user=User.from_json(user) if isinstance(user, dict) else None,
            google_enabled=bool(data.get("googleEnabled")),
            raw=data,
        )

    def login(self, email: str, password: str) -> User:
        """Sign in. Raises with code ``EMAIL_NOT_VERIFIED`` for unconfirmed accounts."""
        data = self.request("/login", {"email": email, "password": password})
        return User.from_json(data.get("user") or {})

    def logout(self) -> None:
        self.request("/logout", {})

    def signup(self, name: str, email: str, password: str) -> Json:
        """Create an account. The server emails a confirmation link."""
        return self.request("/signup", {"name": name, "email": email, "password": password})

    def resend_verification(self, email: str) -> Json:
        return self.request("/resend-verification", {"email": email})

    def forgot_password(self, email: str) -> Json:
        return self.request("/forgot-password", {"email": email})

    def reset_password(self, token: str, password: str) -> Json:
        """``token`` comes from the link in the password-reset email."""
        return self.request("/reset-password", {"token": token, "password": password})

    @property
    def google_login_url(self) -> str:
        """Where a browser starts Google sign-in. Not usable from this client."""
        return f"{self.base_url}/api/auth/google"

    # -- plans -------------------------------------------------------------

    def list_plans(self) -> PlanList:
        data = self.request("/api/plans")
        return PlanList(
            plans=[PlanSummary.from_json(p) for p in data.get("plans") or []],
            new_plan_cost=data.get("cost"),
            coins=data.get("coins"),
            raw=data,
        )

    def get_plan(self, plan_id: str) -> Plan:
        return self._plan(self.request(f"/api/plans/{_seg(plan_id)}"))

    def create_plan(
        self,
        name: str,
        data: str | Mapping[str, Any] | None = None,
        source: str | None = None,
    ) -> Plan:
        """Create a plan. **This costs coins** (see ``Prices.costs["plan"]``).

        Raises with code ``NOT_ENOUGH_COINS`` when the balance is too low.
        ``data`` is an optional starting document; ``source`` is ``"import"``
        when the editor creates a plan from an imported file. Names are capped
        at 60 characters in the editor.
        """
        body: Json = {"name": name}
        if data is not None:
            body["data"] = _document(data)
        if source is not None:
            body["source"] = source
        return self._plan(self.request("/api/plans", body))

    def save_plan(
        self,
        plan_id: str,
        data: str | Mapping[str, Any],
        thumb: str | None = None,
    ) -> Plan:
        """Overwrite a plan's document.

        ``thumb`` is the SVG thumbnail the editor draws for the plan list. The
        editor always sends one; this client leaves the field out when it is
        ``None``, and what the server then does with the old thumbnail is
        untested. The editor spaces saves of one plan 30 seconds apart.
        """
        body: Json = {"data": _document(data)}
        if thumb is not None:
            body["thumb"] = thumb
        return self._plan(self.request(f"/api/plans/{_seg(plan_id)}", body, "PUT"))

    def rename_plan(self, plan_id: str, name: str) -> Plan:
        return self._plan(self.request(f"/api/plans/{_seg(plan_id)}", {"name": name}, "PATCH"))

    # -- sharing -----------------------------------------------------------
    #
    # These manage links for a plan whose sharing is already unlocked in the
    # app. Unlocking costs coins and is not wrapped here. On HTTP 409 the
    # server is asking for a pause: see ``FloorcraftError.retry_in``.

    def new_tour_link(self, plan_id: str) -> Plan:
        """Replace the walkthrough link; the old link stops working."""
        return self._plan(self.request(f"/api/plans/{_seg(plan_id)}/tour-link", {}))

    def new_tour_code(self, plan_id: str) -> Plan:
        """Issue a new access code for a private walkthrough."""
        return self._plan(self.request(f"/api/plans/{_seg(plan_id)}/tour-code", {}))

    def set_tour_private(self, plan_id: str, private: bool) -> Plan:
        return self._plan(
            self.request(f"/api/plans/{_seg(plan_id)}/tour-access", {"private": private})
        )

    def set_tour_domains(self, plan_id: str, domains: list[str]) -> Plan:
        """Set the websites allowed to embed the walkthrough."""
        return self._plan(
            self.request(f"/api/plans/{_seg(plan_id)}/tour-domains", {"domains": domains})
        )

    def new_ar_code(self, plan_id: str) -> Plan:
        """Issue a new access code for a private AR link."""
        return self._plan(self.request(f"/api/plans/{_seg(plan_id)}/ar-code", {}))

    def tour_url(self, plan_id: str, token: str) -> str:
        return f"{self.base_url}/tour/?" + urllib.parse.urlencode({"p": plan_id, "k": token})

    def ar_url(self, plan_id: str, token: str) -> str:
        return f"{self.base_url}/ar/?" + urllib.parse.urlencode({"p": plan_id, "k": token})

    # -- public links (no sign-in) -----------------------------------------

    def open_tour(self, plan_id: str, key: str, code: str = "") -> SharedPlan:
        """Open a shared walkthrough.

        Raises with code ``CODE_REQUIRED`` when the link is private and no
        code was given, and ``WRONG_CODE`` when the code does not match.
        """
        return self._shared("tour", plan_id, key, code)

    def open_ar(self, plan_id: str, key: str, code: str = "") -> SharedPlan:
        """Open a shared AR link. Same errors as :meth:`open_tour`."""
        return self._shared("ar", plan_id, key, code)

    def open_share_url(self, url: str, code: str = "") -> SharedPlan:
        """Open a full ``/tour/?p=…&k=…`` or ``/ar/?p=…&k=…`` link."""
        link = parse_share_url(url)
        return self._shared(link.kind, link.plan_id, link.key, code or link.code)

    def _shared(self, kind: str, plan_id: str, key: str, code: str) -> SharedPlan:
        query = {"k": key}
        if code:
            query["c"] = code
        data = self.request(
            f"/api/public/{kind}/{_seg(plan_id)}?" + urllib.parse.urlencode(query)
        )
        plan = data.get("plan") or {}
        return SharedPlan(name=str(plan.get("name", "")), data=plan.get("data") or "", raw=data)

    # -- coins and purchases (read-only) -----------------------------------

    def prices(self) -> Prices:
        """Coin prices for the caller's region. No sign-in needed."""
        return Prices.from_json(self.request("/api/pay/prices"))

    def coins(self) -> CoinBalance:
        data = self.request("/api/coins")
        return CoinBalance(
            coins=int(data.get("coins", 0)),
            costs=dict(data.get("costs") or {}),
            pricing=data.get("pricing"),
            raw=data,
        )

    def purchase_history(self) -> list[Purchase]:
        data = self.request("/api/pay/history")
        return [Purchase.from_json(p) for p in data.get("purchases") or []]

    def purchase_status(self, purchase_id: str) -> Json:
        """State of one purchase: ``state`` is ``pending``, ``paid`` or ``failed``."""
        return self.request(f"/api/pay/{_seg(purchase_id)}")

    # -- feedback ----------------------------------------------------------

    def send_feedback(self, kind: str, message: str, page: str = "/app/") -> Json:
        """Send the in-app feedback form. ``kind`` is the category picked there."""
        return self.request("/api/feedback", {"kind": kind, "message": message, "page": page})


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _seg(value: str) -> str:
    """Quote a value for use as one URL path segment."""
    return urllib.parse.quote(str(value), safe="")


def _document(data: str | Mapping[str, Any]) -> str:
    """The plan document as the JSON string the server stores."""
    return data if isinstance(data, str) else json.dumps(data, separators=(",", ":"))


def parse_share_url(url: str) -> ShareLink:
    """Split a public ``/tour/``, ``/embed/`` or ``/ar/`` link into its parts."""
    parts = urllib.parse.urlsplit(url)
    path = parts.path.rstrip("/")
    if path == "/ar":
        kind = "ar"
    elif path in ("/tour", "/embed"):
        kind = "tour"
    else:
        raise ValueError(f"not a Floorcraft share link: {url}")
    query = urllib.parse.parse_qs(parts.query)
    plan_id = (query.get("p") or [""])[0]
    key = (query.get("k") or [""])[0]
    if not plan_id or not key:
        raise ValueError(f"share link is missing its p or k parameter: {url}")
    return ShareLink(kind=kind, plan_id=plan_id, key=key, code=(query.get("c") or [""])[0])


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------

_USAGE = """usage: floorcraft_client.py <command> [args]

  prices              coin prices and action costs (no sign-in)
  me                  who is signed in
  login               sign in with FLOORCRAFT_EMAIL / FLOORCRAFT_PASSWORD
  plans               list your floor plans
  plan <id> [file]    print a plan's document, or write it to a file
  share <url> [code]  open a public walkthrough or AR link

The session is kept in the file named by FLOORCRAFT_COOKIES
(default: ./floorcraft.cookies). Do not commit that file.
"""


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(_USAGE)
        return 0 if argv else 2
    command, args = argv[0], argv[1:]
    fc = FloorcraftClient(cookie_file=os.environ.get("FLOORCRAFT_COOKIES", "floorcraft.cookies"))
    try:
        if command == "prices":
            print(json.dumps(fc.prices().raw, indent=2))
        elif command == "me":
            print(json.dumps(fc.me().raw, indent=2))
        elif command == "login":
            email = os.environ.get("FLOORCRAFT_EMAIL")
            password = os.environ.get("FLOORCRAFT_PASSWORD")
            if not email or not password:
                print("set FLOORCRAFT_EMAIL and FLOORCRAFT_PASSWORD first", file=sys.stderr)
                return 2
            user = fc.login(email, password)
            print(f"signed in as {user.name} ({user.coins} coins)")
        elif command == "plans":
            listing = fc.list_plans()
            for plan in listing.plans:
                print(f"{plan.id}\t{plan.updated_at}\t{plan.name}")
            print(f"{len(listing.plans)} plan(s), {listing.coins} coins", file=sys.stderr)
        elif command == "plan" and args:
            document = json.dumps(fc.get_plan(args[0]).document(), indent=2)
            if len(args) > 1:
                with open(args[1], "w", encoding="utf-8") as handle:
                    handle.write(document)
            else:
                print(document)
        elif command == "share" and args:
            shared = fc.open_share_url(args[0], args[1] if len(args) > 1 else "")
            print(json.dumps({"name": shared.name, "document": shared.document()}, indent=2))
        else:
            print(_USAGE, file=sys.stderr)
            return 2
    except FloorcraftError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
