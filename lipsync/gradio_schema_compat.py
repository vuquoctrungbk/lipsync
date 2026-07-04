"""Compatibility shim for a gradio_client 1.3.0 API-schema crash.

gradio 4.44.1 ships gradio_client 1.3.0, whose ``get_type()`` runs
``if "const" in schema:`` without checking that ``schema`` is a dict. JSON
Schema allows boolean nodes (e.g. ``additionalProperties: true``), and this
app's File/Audio/Image components emit exactly such a schema. When Gradio
builds its API info at launch (``get_api_info()``), the recursion reaches that
boolean node and raises ``TypeError: argument of type 'bool' is not iterable``.

Consequences without this shim:
- Gradio's ``/info`` route 500s on every hit -> the console error flood.
- Gradio's own localhost health-check then fails, so ``launch()`` aborts with
  ``ValueError: When localhost is not accessible, a shareable link must be
  created`` -> the UI never comes up.

Upgrading gradio/gradio_client would ripple through a deliberately pinned,
fragile dependency stack (see requirements.txt), so we wrap only the one buggy
function to treat a non-dict schema node as its JSON-Schema type. Idempotent,
and a no-op once the installed gradio_client no longer exposes the 1.3.0 shape.
"""
from __future__ import annotations

# Marker set on the wrapper so re-imports / repeated apply() calls don't stack.
_PATCH_FLAG = "_lipsync_bool_schema_safe"


def apply() -> None:
    """Make gradio_client's schema walker tolerate boolean JSON-Schema nodes.

    Safe to call any number of times. No-op if gradio_client is absent or has
    already been patched.
    """
    try:
        from gradio_client import utils as gcu
    except Exception:  # gradio_client not installed -> nothing to patch
        return

    original = getattr(gcu, "get_type", None)
    if original is None or getattr(original, _PATCH_FLAG, False):
        return  # not the expected shape, or already patched

    def get_type(schema):
        # JSON Schema permits boolean nodes (additionalProperties: true/false).
        # The original does `"const" in schema`, which explodes on a bool; map a
        # bool to its type and any other non-dict to {} (-> "Any" downstream).
        if isinstance(schema, bool):
            return "boolean"
        if not isinstance(schema, dict):
            return {}
        return original(schema)

    setattr(get_type, _PATCH_FLAG, True)
    gcu.get_type = get_type
