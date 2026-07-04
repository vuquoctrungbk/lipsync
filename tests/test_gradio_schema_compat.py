"""Regression for the gradio_client 1.3.0 boolean-schema crash.

Upstream ``get_type()`` runs ``"const" in schema`` without a dict check, so a
boolean JSON-Schema node (``additionalProperties: true`` — exactly what this
app's File/Audio/Image components emit) raises
``TypeError: argument of type 'bool' is not iterable`` while Gradio builds its
API info at launch. Without the shim these tests fail with that TypeError.
"""
import pytest

from lipsync import gradio_schema_compat


def test_bool_additional_properties_does_not_crash():
    gcu = pytest.importorskip("gradio_client.utils")
    gradio_schema_compat.apply()

    # Object schema whose additionalProperties is a bare boolean node.
    schema = {"type": "object", "additionalProperties": True}
    result = gcu.json_schema_to_python_type(schema)  # must not raise

    assert isinstance(result, str)
    assert "bool" in result  # the boolean node resolved to a Python bool type


def test_apply_is_idempotent():
    gcu = pytest.importorskip("gradio_client.utils")
    gradio_schema_compat.apply()
    patched = gcu.get_type
    gradio_schema_compat.apply()
    assert gcu.get_type is patched  # second apply() must not re-wrap
