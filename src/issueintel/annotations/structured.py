"""Optional strict structured output; validation remains mandatory afterward."""

import json

from .models import AnnotationError
from issueintel.investigation.providers import ProviderError


def obj(properties):
    return {"type":"object", "properties":properties, "required":list(properties), "additionalProperties":False}


def text():
    return {"type":"string"}


def ids(values, *, minimum=0, maximum=None):
    result={"type":"array", "items":{"type":"string", "enum":list(values)}, "minItems":minimum}
    if maximum is not None:
        result["maxItems"]=maximum
    return result


def generation_schema(evidence_ids):
    hypothesis = obj({"id":{"type":"string","enum":["H1","H2","H3","H4","H5"]}, "statement":text(), "support_ids":ids(evidence_ids,minimum=1),
        "counter_ids":ids(evidence_ids), "counter_status":{"type":"string","enum":["found","not_found"]},
        "falsification_test":text()})
    return obj({"hypotheses":{"type":"array","items":hypothesis,"minItems":3,"maxItems":5},
        "abstention":obj({"label":{"type":"string","enum":["answerable","abstain","uncertain"]},"reason":text()})})


def judgment_schema(hypothesis_ids, gold_ids, hunk_ids=None):
    # Leave evidence-kind semantics to app validation. Groq's anyOf discriminator
    # and nested enum validation proved unreliable on real requests; no label is
    # accepted merely because its output schema is valid.
    return obj({"judgments":{"type":"array","items":obj({
        "hypothesis_id":{"type":"string","enum":list(hypothesis_ids)},
        "label":{"type":"string","enum":["supported","refuted","uncertain"]},
        "reason":text(),"gold_evidence_ids":ids(gold_ids)}),
        "minItems":len(hypothesis_ids),"maxItems":len(hypothesis_ids)}})


def critic_schema(hypothesis_ids, evidence_ids):
    return obj({"reviews":{"type":"array","items":obj({"hypothesis_id":{"type":"string","enum":list(hypothesis_ids)},
        "assessment":{"type":"string","enum":["supported","unsupported","contradicted","uncertain"]},
        "reason":text(),"evidence_ids":ids(evidence_ids)})}})


def can_fallback_to_json_object(error, complete_json):
    """Classify a strict-output failure; this function never makes a call.

    The annotation caller owns the single repair slot. Other provider failures,
    including an unsupported request schema, remain terminal.
    """
    return (isinstance(error, ProviderError) and error.status == 400
            and error.code == "json_validate_failed"
            and callable(getattr(complete_json, "complete_structured", None))
            and callable(getattr(complete_json, "complete_json", None)))


def json_object_repair_messages(messages, schema):
    # The rejected provider output is neither exposed nor used as evidence.
    return messages + [{"role": "user", "content": json.dumps({
        "validation_error": "Provider rejected strict output (400/json_validate_failed).",
        "instruction": "Return a fresh JSON object from the original supplied evidence. Follow the complete required schema below and copy exact supplied citation IDs only. Do not invent, abbreviate, rename or guess citations. This is the only repair attempt.",
        "required_schema": schema,
    }, ensure_ascii=False, allow_nan=False)}]


def _validate_wire_schema(value, schema, path="response"):
    """Preserve our strict schema subset when constrained decoding fails.

    This supplements, never replaces, citation and semantic app validation.
    Errors expose a field path rather than provider content or source text.
    """
    kind = schema["type"]
    if kind == "object":
        if not isinstance(value, dict):
            raise AnnotationError(f"{path} must be an object")
        properties = schema["properties"]
        if set(schema["required"]) - set(value):
            raise AnnotationError(f"{path} is missing required fields")
        if schema.get("additionalProperties") is False and set(value) - set(properties):
            raise AnnotationError(f"{path} has unexpected fields")
        for key, item in value.items():
            _validate_wire_schema(item, properties[key], f"{path}.{key}")
    elif kind == "array":
        if not isinstance(value, list):
            raise AnnotationError(f"{path} must be an array")
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", len(value)):
            raise AnnotationError(f"{path} has invalid array length")
        for index, item in enumerate(value):
            _validate_wire_schema(item, schema["items"], f"{path}[{index}]")
        if schema.get('uniqueItems') is True and len({json.dumps(item,sort_keys=True,ensure_ascii=False,allow_nan=False) for item in value})!=len(value):
            raise AnnotationError(f"{path} has duplicate items")
    elif kind == "string":
        if not isinstance(value, str):
            raise AnnotationError(f"{path} must be a string")
        if "enum" in schema and value not in schema["enum"]:
            raise AnnotationError(f"{path} is outside its allowed enum")
        if len(value) < schema.get('minLength', 0) or len(value) > schema.get('maxLength', len(value)):
            raise AnnotationError(f"{path} has invalid string length")
    else:
        raise AnnotationError("Unsupported local output schema type")


def dispatch(complete_json, messages, *, max_output_tokens, schema, json_object=False):
    if json_object:
        response = complete_json.complete_json(messages, max_output_tokens=max_output_tokens)
        if isinstance(response, str):
            try:
                response = json.loads(response)
            except ValueError as error:
                raise AnnotationError("Fallback did not return valid JSON") from error
        _validate_wire_schema(response, schema)
        return response
    structured = getattr(complete_json, "complete_structured", None)
    if callable(structured):
        return structured(messages, max_output_tokens=max_output_tokens, schema=schema)
    return complete_json(messages, max_output_tokens=max_output_tokens)
