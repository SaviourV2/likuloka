# Floorcraft API client

`floorcraft_client.py` is a typed Python client for the private HTTP API behind [Floorcraft](https://floorcraft.chirostudio.xyz), the browser floor plan editor. Floorcraft publishes no API, so the client mirrors the calls the editor makes to its own `/api` backend.

It uses the standard library only and needs Python 3.10 or newer.

## Use

```python
from floorcraft_client import FloorcraftClient, FloorcraftError

fc = FloorcraftClient(cookie_file="floorcraft.cookies")
fc.login("you@example.com", "password")

listing = fc.list_plans()
for plan in listing.plans:
    print(plan.id, plan.name, plan.updated_at)

plan = fc.get_plan(listing.plans[0].id)
document = plan.document()          # the editor's document as a dict
fc.save_plan(plan.id, document)     # write it back
```

From a shell:

```bash
python floorcraft_client.py prices
```

```bash
FLOORCRAFT_EMAIL=you@example.com FLOORCRAFT_PASSWORD=... python floorcraft_client.py login
```

```bash
python floorcraft_client.py plans
```

```bash
python floorcraft_client.py plan <plan id> plan.json
```

```bash
python floorcraft_client.py share "https://floorcraft.chirostudio.xyz/tour/?p=...&k=..."
```

## Signing in

The API authenticates with a session cookie. `login()` gets one from an email and password. Pass `cookie_file` and the session is written to that file and reloaded on the next run. The command line keeps it in `./floorcraft.cookies`, or the path in `FLOORCRAFT_COOKIES`.

Accounts that sign in with Google have no password to send. For those, copy the `Cookie` request header from a signed-in browser tab (developer tools, Network, any `/api/` request) and pass it as `FloorcraftClient(cookie="...")`.

The cookie file is a login. It is listed in `.gitignore`; keep it out of commits and chats.

## Endpoints

| Method | Call | Sign-in |
|---|---|---|
| `me()` | `GET /api/auth/me` | no |
| `login(email, password)` | `POST /api/auth/login` | no |
| `logout()` | `POST /api/auth/logout` | yes |
| `signup(name, email, password)` | `POST /api/auth/signup` | no |
| `resend_verification(email)` | `POST /api/auth/resend-verification` | no |
| `forgot_password(email)` | `POST /api/auth/forgot-password` | no |
| `reset_password(token, password)` | `POST /api/auth/reset-password` | no |
| `list_plans()` | `GET /api/plans` | yes |
| `get_plan(id)` | `GET /api/plans/{id}` | yes |
| `create_plan(name, data, source)` | `POST /api/plans` | yes |
| `save_plan(id, data, thumb)` | `PUT /api/plans/{id}` | yes |
| `rename_plan(id, name)` | `PATCH /api/plans/{id}` | yes |
| `new_tour_link(id)` | `POST /api/plans/{id}/tour-link` | yes |
| `new_tour_code(id)` | `POST /api/plans/{id}/tour-code` | yes |
| `set_tour_private(id, private)` | `POST /api/plans/{id}/tour-access` | yes |
| `set_tour_domains(id, domains)` | `POST /api/plans/{id}/tour-domains` | yes |
| `new_ar_code(id)` | `POST /api/plans/{id}/ar-code` | yes |
| `open_tour(id, key, code)` | `GET /api/public/tour/{id}?k=&c=` | no |
| `open_ar(id, key, code)` | `GET /api/public/ar/{id}?k=&c=` | no |
| `prices()` | `GET /api/pay/prices` | no |
| `coins()` | `GET /api/coins` | yes |
| `purchase_history()` | `GET /api/pay/history` | yes |
| `purchase_status(id)` | `GET /api/pay/{id}` | yes |
| `send_feedback(kind, message, page)` | `POST /api/feedback` | not checked |

Failed calls raise `FloorcraftError` with the HTTP `status`, the server's `code` and its `message`. `NotSignedIn` (401) and `RateLimited` (429) are subclasses. The codes the editor handles are `NOT_SIGNED_IN`, `EMAIL_NOT_VERIFIED`, `NOT_ENOUGH_COINS`, `CODE_REQUIRED`, `WRONG_CODE` and `NOT_FOUND`. On a 409 from a sharing call, `err.retry_in` is the number of seconds the server asks you to wait.

## Coins

Floorcraft charges coins for some actions, and the server applies those charges whoever the caller is. `create_plan()` costs coins (2 at the time of writing; `prices().costs["plan"]` has the current number) and fails with `NOT_ENOUGH_COINS` when the balance is short. A new account starts with 6.

The sharing calls only work on a plan whose walkthrough or AR sharing is already unlocked in the app.

Three endpoints are left out on purpose: buying coins (`/api/pay/checkout`), spending coins to unlock a feature (`/api/coins/spend`) and the developer grant (`/api/coins/dev-grant`). Do those in the app.

## Plan documents

`Plan.data` is a JSON string, and `Plan.document()` parses it. The top level looks like `{"v": 2, "plan": ..., "gen": ...}`, with optional `layout`, `lights`, `electrical`, `materials`, `fx`, `section`, `ar` and `tour` keys. The client does not model anything below that level.

The editor spaces saves of one plan 30 seconds apart, and it sends an SVG thumbnail with each save. `save_plan()` leaves the thumbnail out unless you pass one.

## Verified

The client was written by reading the editor's JavaScript bundle on 2026-11-15. No signed-in browser session was recorded.

Tested against the live site: `prices()`, `me()` signed out, the 401 answer from `list_plans()`, `get_plan()`, `coins()` and `purchase_history()`, and the 404 answer from `open_tour()` and `open_ar()` for a link that does not exist.

Not tested: every call that needs an account, and a successful `open_tour()` or `open_ar()`. Their paths, methods and request bodies are copied from the editor. The response fields on the models are the ones the editor reads, so a model may miss fields the server also sends; the full JSON is always in `.raw`.

The site can change its API without notice. If a call starts failing, compare it with the current editor bundle.

Use the client on your own account and within Floorcraft's terms.
