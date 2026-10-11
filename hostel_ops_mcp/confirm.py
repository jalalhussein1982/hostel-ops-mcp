"""Human confirmation of a destructive action through a Multi Round-Trip Request.

Protocol 2026-07-28 retired server-initiated elicitation. A tool that needs a yes from a
human returns an ``InputRequiredResult`` carrying a form elicitation and an opaque
``request_state``; the host asks, then retries the same call with ``input_responses``
and the echoed state. ``confirm_or_request`` is the whole dance from the tool's side:

* no answer yet (or an unusable one)  -> ``ask``: return the ``InputRequiredResult``
* answer with action != accept        -> ``declined``: the tool returns ``Cancelled``
* accept with ``confirm: true``       -> ``confirmed``: the tool acts

The SDK seals ``request_state`` (AES-GCM, bound to the tool name, the argument digest,
the authenticated principal and a TTL) before it crosses the wire and verifies the echo
before this code runs, so a retry with other arguments or a tampered token never reaches
the handler: it fails with -32602. The plaintext here is only a second, in-handler check.

Hosts that declare no form elicitation capability cannot answer, so they never get the
question: the tool refuses with a ``ToolError`` naming the admin panel.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ElicitRequest, ElicitRequestFormParams, ElicitResult, InputRequiredResult

NO_ELICITATION = "this host cannot confirm destructive actions; use the admin panel"

CONFIRM_SCHEMA = {
    "type": "object",
    "properties": {
        "confirm": {"type": "boolean", "title": "Confirm", "description": "Tick to proceed. Leave unticked to cancel."},
    },
    "required": ["confirm"],
}


@dataclass
class ConfirmOutcome:
    status: Literal["confirmed", "declined", "ask"]
    result: InputRequiredResult | None = None
    action: str | None = None


def host_supports_form_elicitation(ctx: Context) -> bool:
    """Form elicitation is declared by `elicitation.form`, or by a bare `elicitation: {}`.

    The 2025-06-18 revision had form mode only, declared as an empty object; `form` and `url`
    sub-capabilities arrived in 2025-11-25. A client that declares only `url` cannot render our
    boolean form and is refused."""
    caps = ctx.client_capabilities
    if caps is None or caps.elicitation is None:
        return False
    el = caps.elicitation
    if el.form is not None:
        return True
    return el.url is None   # bare `elicitation: {}` -> legacy form-only client


def confirm_or_request(ctx: Context, key: str, message: str, state: str,
                       supports_elicitation: bool | None = None) -> ConfirmOutcome:
    """Decide whether the human has confirmed; otherwise build the question.

    ``state`` is the plaintext the tool wants echoed (e.g. ``"erase:<token>"``). It
    is compared with ``ctx.request_state`` on the retry as a belt-and-braces check on top
    of the SDK's sealed binding.
    """
    if supports_elicitation is None:
        supports_elicitation = host_supports_form_elicitation(ctx)
    if not supports_elicitation:
        raise ToolError(NO_ELICITATION)

    responses = ctx.input_responses or {}
    answer = responses.get(key)
    if isinstance(answer, ElicitResult) and ctx.request_state == state:
        if answer.action != "accept":
            return ConfirmOutcome("declined", action=answer.action)
        if answer.content is not None and answer.content.get("confirm") is True:
            return ConfirmOutcome("confirmed", action="accept")
        # accepted the form but did not tick the box, or sent nothing usable: ask again
    request = ElicitRequest(params=ElicitRequestFormParams(message=message, requested_schema=CONFIRM_SCHEMA))
    return ConfirmOutcome("ask", result=InputRequiredResult(input_requests={key: request}, request_state=state))
