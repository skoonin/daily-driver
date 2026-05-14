"""Generate `.dd-config.yaml.j2` from the Config pydantic models.

Single source of truth for the workspace config template. Run via
`make config-template`; verified fresh by `make check-config-template`.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any, cast, get_args, get_origin

from pydantic import BaseModel
from pydantic.fields import FieldInfo

from daily_driver.core.config_models import Config

_PREAMBLE = """\
# daily-driver workspace configuration
#
# Structured settings live here; narrative prose stays in Markdown files
# (context.md, voice-profile.md). Top-level keys not listed below are
# preserved on parse — the root model is `extra="allow"` so you can stash
# workspace-local notes without a schema bump. Plugin schemas are strict
# (`extra="forbid"`); typos there are real errors.
#
# To enable a commented block: uncomment the header (at column 0) AND
# every nested line together. Don't uncomment a single inner field on
# its own — YAML will silently nest it inside whichever open block is
# above, and Pydantic will reject it as `extra_forbidden`.
"""


def _extra(info: FieldInfo) -> dict[str, Any]:
    extra = info.json_schema_extra
    return dict(extra) if isinstance(extra, dict) else {}


def _is_model_type(tp: Any) -> bool:
    try:
        return isinstance(tp, type) and issubclass(tp, BaseModel)
    except TypeError:
        return False


def _unwrap_model(annotation: Any) -> type[BaseModel] | None:
    """Unwrap `Model` and `Optional[Model]` annotations to the BaseModel class.

    Returns None for container annotations like `list[Model]` or
    `dict[str, Model]` — those are handled by separate helpers.
    """
    if _is_model_type(annotation):
        return cast("type[BaseModel]", annotation)
    origin = get_origin(annotation)
    if origin is None:
        return None
    args = get_args(annotation)
    if origin in (list, dict, tuple, set, frozenset):
        return None
    non_none = [a for a in args if a is not type(None)]
    if len(non_none) == 1 and _is_model_type(non_none[0]):
        return cast("type[BaseModel]", non_none[0])
    return None


def _quote_string(value: str) -> str:
    """Render a string with explicit double quotes, escaping as needed."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _format_scalar(value: Any, *, force_quote: bool = False) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, datetime.date) and not isinstance(value, datetime.datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return _format_string_value(str(value), force_quote=force_quote)
    if isinstance(value, str):
        return _format_string_value(value, force_quote=force_quote)
    raise TypeError(f"unsupported scalar: {type(value).__name__}={value!r}")


_BARE_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-./")


def _format_string_value(value: str, *, force_quote: bool = False) -> str:
    if value == "":
        return '""'
    if force_quote:
        return _quote_string(value)
    if any(ch not in _BARE_OK for ch in value):
        return _quote_string(value)
    if value in {"true", "false", "null", "yes", "no", "on", "off"}:
        return _quote_string(value)
    if value[0].isdigit() or value[0] in "-+":
        try:
            int(value)
            return _quote_string(value)
        except ValueError:
            pass
        try:
            float(value)
            return _quote_string(value)
        except ValueError:
            pass
    return value


def _format_inline_list(value: list[Any]) -> str:
    if not value:
        return "[]"
    parts = [_format_scalar(v) for v in value]
    return "[" + ", ".join(parts) + "]"


def _wrap_comment(text: str, indent: str = "") -> list[str]:
    lines: list[str] = []
    for raw in text.split("\n"):
        if raw == "":
            lines.append(f"{indent}#")
        else:
            lines.append(f"{indent}# {raw}")
    return lines


def _comment_prefix_lines(lines: list[str], indent: str = "") -> list[str]:
    """Prefix every non-empty line with `# ` at the given indent.

    Lines already deeper than `indent` keep that extra indent inside the
    comment so nested structure remains visually intact. Lines that already
    begin with `#` at this indent are left alone (avoids double-prefixing
    when a nested template_commented field is wrapped by its parent).
    """
    out: list[str] = []
    for ln in lines:
        if ln == "":
            out.append("#")
            continue
        if ln.startswith(f"{indent}# ") or ln == f"{indent}#":
            out.append(ln)
            continue
        if ln.startswith(indent):
            rest = ln[len(indent) :]
            out.append(f"{indent}# {rest}")
        else:
            out.append(f"# {ln}")
    return out


def _render_value(
    value: Any,
    indent: str,
    *,
    inline_quote_strings: bool = False,
) -> list[str]:
    """Render an arbitrary value (dict/list/scalar) as YAML lines.

    The first line does NOT include any leading key — callers prepend
    `<key>: ` or `<key>:` themselves.
    """
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        if not value:
            return ["{}"]
        out: list[str] = []
        for k, v in value.items():
            out.extend(_render_dict_entry(k, v, indent))
        return out
    if isinstance(value, list):
        if not value:
            return ["[]"]
        return _render_list(value, indent)
    return [_format_scalar(value, force_quote=inline_quote_strings)]


def _render_dict_entry(key: str, value: Any, indent: str) -> list[str]:
    if isinstance(value, dict):
        if not value:
            return [f"{indent}{key}: " + "{}"]
        out = [f"{indent}{key}:"]
        for k, v in value.items():
            out.extend(_render_dict_entry(k, v, indent + "  "))
        return out
    if isinstance(value, list):
        if not value:
            return [f"{indent}{key}: []"]
        # Inline scalar lists by default; block-form for lists of dicts.
        if all(not isinstance(v, (dict, list)) for v in value):
            return [f"{indent}{key}: {_format_inline_list(value)}"]
        out = [f"{indent}{key}:"]
        out.extend(_render_list(value, indent))
        return out
    return [f"{indent}{key}: {_format_scalar(value)}"]


def _render_list(value: list[Any], indent: str) -> list[str]:
    out: list[str] = []
    for item in value:
        if isinstance(item, dict):
            first = True
            for k, v in item.items():
                prefix = "- " if first else "  "
                first = False
                if isinstance(v, (dict, list)):
                    out.append(f"{indent}{prefix}{k}:")
                    out.extend(_render_value(v, indent + "    "))
                else:
                    out.append(f"{indent}{prefix}{k}: {_format_scalar(v)}")
        else:
            out.append(f"{indent}- {_format_scalar(item)}")
    return out


def _field_effective_value(info: FieldInfo) -> tuple[str, Any]:
    """Return ('example' | 'default', value) for what to render for a field."""
    extra = _extra(info)
    if "template_example" in extra:
        return ("example", extra["template_example"])
    if extra.get("template_example_model"):
        return ("example_model", None)
    if "template_default" in extra:
        return ("default", extra["template_default"])
    return ("default", info.default)


def _render_model_field(
    name: str,
    info: FieldInfo,
    indent: str,
) -> list[str]:
    extra = _extra(info)
    if extra.get("template_skip"):
        return []

    block_comment = extra.get("block_comment")
    description = info.description or ""
    inline_comment = extra.get("inline_comment")

    annotation = info.annotation
    sub_model_cls = _unwrap_model(annotation)

    kind, value = _field_effective_value(info)

    value_lines: list[str]

    if kind == "example_model" and sub_model_cls is not None:
        value_lines = [f"{indent}{name}:"]
        value_lines.extend(_render_model_body(sub_model_cls, indent + "  "))
    elif kind == "example":
        # An explicit example overrides the default. If the field is a
        # nested model, descend into the model with the example as overrides.
        if sub_model_cls is not None and isinstance(value, dict):
            value_lines = [f"{indent}{name}:"]
            value_lines.extend(
                _render_model_body(sub_model_cls, indent + "  ", overrides=value)
            )
        else:
            value_lines = _render_keyed_value(name, value, info, indent)
    else:
        # Default-value path.
        if sub_model_cls is not None and isinstance(value, BaseModel):
            value_lines = [f"{indent}{name}:"]
            value_lines.extend(_render_model_body(sub_model_cls, indent + "  "))
        elif isinstance(value, dict) and sub_model_cls is None:
            if not value:
                value_lines = [f"{indent}{name}: " + "{}"]
            else:
                value_lines = _render_dict_default(name, value, info, indent)
        else:
            value_lines = _render_keyed_value(name, value, info, indent)

    if inline_comment and value_lines:
        first = value_lines[0]
        if not first.endswith(":") and "  #" not in first:
            value_lines[0] = f"{first}  # {inline_comment}"

    body: list[str] = []
    if description:
        body.extend(_wrap_comment(description, indent=indent))
    body.extend(value_lines)

    trailing = extra.get("trailing_comment")
    if trailing:
        trailing_indent_extra = extra.get("trailing_comment_indent", 0)
        body.extend(
            _wrap_comment(trailing, indent=indent + " " * trailing_indent_extra)
        )

    if extra.get("template_commented"):
        body = _comment_prefix_lines(body, indent=indent)

    out: list[str] = []
    if block_comment:
        out.extend(_wrap_comment(block_comment, indent=indent))
    out.extend(body)
    return out


def _render_keyed_value(
    name: str, value: Any, info: FieldInfo, indent: str
) -> list[str]:
    extra = _extra(info)
    force_quote = bool(extra.get("template_quote"))

    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        if not value:
            return [f"{indent}{name}: " + "{}"]
        out = [f"{indent}{name}:"]
        inline_comments = extra.get("template_example_inline_comments", {})
        block_comments = extra.get("template_example_block_comments", {})
        for k, v in value.items():
            if k in block_comments:
                out.append("")
                out.extend(_wrap_comment(block_comments[k], indent=indent + "  "))
            if isinstance(v, (dict, list)):
                out.extend(_render_dict_entry(k, v, indent + "  "))
            else:
                line = f"{indent}  {k}: {_format_scalar(v)}"
                if k in inline_comments:
                    line = f"{line}            # {inline_comments[k]}"
                out.append(line)
        return out
    if isinstance(value, list):
        if not value:
            return [f"{indent}{name}: []"]
        force_block = bool(extra.get("template_block_list"))
        if not force_block and all(not isinstance(v, (dict, list)) for v in value):
            return [f"{indent}{name}: {_format_inline_list(value)}"]
        out = [f"{indent}{name}:"]
        if all(not isinstance(v, (dict, list)) for v in value):
            for item in value:
                # Force-quote example block-list scalars to match the original
                # template style for `search_paths: ["~/git"]`.
                out.append(f"{indent}  - {_format_scalar(item, force_quote=True)}")
            return out
        inline_by_idx = extra.get("template_example_inline_comments_by_index", {})
        for idx, item in enumerate(value):
            if isinstance(item, dict):
                per_item = inline_by_idx.get(idx, {})
                first = True
                for k, v in item.items():
                    prefix = "- " if first else "  "
                    first = False
                    inline = per_item.get(k)
                    if isinstance(v, (dict, list)):
                        out.append(f"{indent}  {prefix}{k}:")
                        out.extend(_render_value(v, indent + "      "))
                    else:
                        line = f"{indent}  {prefix}{k}: {_format_scalar(v)}"
                        if inline:
                            line = f"{line}            # {inline}"
                        out.append(line)
            else:
                out.append(f"{indent}  - {_format_scalar(item)}")
        return out

    # Scalar. Quote strings if annotation is `str` (vs `str | None`),
    # to match the original template style where string defaults were
    # double-quoted unless they were enums/literal-like.
    quote = force_quote
    if isinstance(value, str) and not quote:
        # Heuristic: leave bare for enum-like values (no spaces, all alnum/-),
        # double-quote everything else with spaces or punctuation.
        if any(ch.isspace() for ch in value):
            quote = True
    return [f"{indent}{name}: {_format_scalar(value, force_quote=quote)}"]


def _render_dict_default(
    name: str, value: dict[str, Any], info: FieldInfo, indent: str
) -> list[str]:
    """dict[str, SomeModel] with concrete default entries."""
    out = [f"{indent}{name}:"]
    annotation = info.annotation
    # Determine the value model class for the dict.
    value_cls: type[BaseModel] | None = None
    if get_origin(annotation) is dict:
        args = get_args(annotation)
        if len(args) == 2:
            value_cls = _unwrap_model(args[1])
    for k, v in value.items():
        if isinstance(v, BaseModel) and value_cls is not None:
            out.append(f"{indent}  {k}:")
            out.extend(_render_model_body(value_cls, indent + "    ", instance=v))
        else:
            out.extend(_render_dict_entry(k, v, indent + "  "))
    return out


def _render_model_body(
    model_cls: type[BaseModel],
    indent: str,
    *,
    instance: BaseModel | None = None,
    overrides: dict[str, Any] | None = None,
) -> list[str]:
    """Render the body of a model (no leading key) using field metadata.

    `instance` supplies non-default values for individual fields (used when
    we have a concrete model instance to render, e.g. categories dict
    entries). `overrides` provides a dict of values that should replace
    field defaults (used for example_model rendering with a custom example).
    """
    out: list[str] = []
    for name, info in model_cls.model_fields.items():
        extra = _extra(info)
        if extra.get("template_skip"):
            continue
        if overrides is not None and name in overrides:
            override_val = overrides[name]
            sub_cls = _unwrap_model(info.annotation)
            if sub_cls is not None and isinstance(override_val, dict):
                lines = [f"{indent}{name}:"] + _render_model_body(
                    sub_cls, indent + "  ", overrides=override_val
                )
            else:
                # Build a synthetic FieldInfo for value rendering?
                # Just render directly with helpers, carrying inline_comment.
                lines = _render_keyed_value(name, override_val, info, indent)
            description = info.description or ""
            inline_comment = extra.get("inline_comment")
            if inline_comment and lines and not lines[0].endswith(":"):
                lines[0] = f"{lines[0]}  # {inline_comment}"
            head: list[str] = []
            if extra.get("block_comment"):
                head.extend(_wrap_comment(extra["block_comment"], indent=indent))
            if description:
                head.extend(_wrap_comment(description, indent=indent))
            out.extend(head + lines)
            continue
        if instance is not None:
            # Render with the instance's value for this field.
            value = getattr(instance, name)
            lines = _render_keyed_value(name, value, info, indent)
            out.extend(lines)
            continue
        out.extend(_render_model_field(name, info, indent))
    return out


def render_config_template() -> str:
    chunks: list[str] = [_PREAMBLE]
    first = True
    for name, info in Config.model_fields.items():
        rendered = _render_model_field(name, info, indent="")
        if not rendered:
            continue
        if not first:
            chunks.append("")
        first = False
        chunks.extend(rendered)
    return "\n".join(chunks) + "\n"


def main() -> None:
    import sys

    sys.stdout.write(render_config_template())


if __name__ == "__main__":
    main()
