#!/usr/bin/env python3
"""Check InkRelay's concrete YAML subset and local references; no dependencies.

This is not a general YAML parser, a writing score, or a model behavior test.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit


class ValidationError(ValueError):
    pass


def parse_subset(text: str) -> dict[str, object]:
    """Accept mappings with quoted strings and bare name/license scalars only."""
    result: dict[str, object] = {}
    parent: dict[str, object] | None = None
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"( {0}| {2})([a-z_]+):(?: (.*))?", line)
        if not match:
            raise ValidationError(f"unsupported YAML subset at line {number}")
        indent, key, raw = match.groups()
        if not indent:
            target = result
            parent = None
        elif parent is not None:
            target = parent
        else:
            raise ValidationError(f"unexpected indentation at line {number}")
        if key in target:
            raise ValidationError(f"duplicate key: {key}")
        if raw is None:
            if indent:
                raise ValidationError("nested mappings deeper than one level unsupported")
            child: dict[str, object] = {}
            target[key] = child
            parent = child
        elif raw.startswith('"'):
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as error:
                raise ValidationError(f"invalid quoted string at line {number}") from error
            if not isinstance(value, str):
                raise ValidationError("expected string")
            target[key] = value
        elif not indent and key in {"name", "license"} and re.fullmatch(r"[a-zA-Z0-9_-]+", raw):
            target[key] = raw
        else:
            raise ValidationError(f"unsupported scalar at line {number}")
    return result


def require_string(mapping: dict[str, object], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise ValidationError(f"missing or invalid string: {key}")
    return value


def headings(text: str) -> set[str]:
    slugs: set[str] = set()
    duplicates: dict[str, int] = {}
    for title in re.findall(r"^#{1,6}\s+(.+?)\s*#*\s*$", text, re.MULTILINE):
        slug = re.sub(r"[^\w\s-]", "", title.lower()).replace(" ", "-")
        count = duplicates.get(slug, 0)
        duplicates[slug] = count + 1
        slugs.add(f"{slug}-{count}" if count else slug)
    return slugs


def read_inside(root: Path, path: Path) -> str:
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise ValidationError(f"file resolves outside repository: {path}")
    return resolved.read_text(encoding="utf-8")


def validate(root: Path) -> dict[str, object]:
    root = root.resolve(strict=True)
    skill = root / "skills" / "inkrelay"
    text = read_inside(root, skill / "SKILL.md")
    match = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
    if not match:
        raise ValidationError("missing frontmatter")
    metadata = parse_subset(match.group(1))
    if set(metadata) != {"name", "description", "license", "metadata"}:
        raise ValidationError("unexpected frontmatter fields")
    if require_string(metadata, "name") != "inkrelay":
        raise ValidationError("skill name changed")
    description = require_string(metadata, "description")
    if len(description) > 1024 or "<" in description or ">" in description:
        raise ValidationError("invalid description")
    if require_string(metadata, "license") != "MIT":
        raise ValidationError("license changed")
    nested = metadata["metadata"]
    if not isinstance(nested, dict):
        raise ValidationError("metadata must be a mapping")
    version = require_string(nested, "version")
    if not re.fullmatch(r"0\.0\.\d+", version):
        raise ValidationError("invalid version")
    ui = parse_subset(read_inside(root, skill / "agents" / "openai.yaml"))
    interface = ui.get("interface")
    if not isinstance(interface, dict):
        raise ValidationError("missing UI interface")
    require_string(interface, "display_name")
    short = require_string(interface, "short_description")
    if not 25 <= len(short) <= 64:
        raise ValidationError("UI short description outside 25–64 characters")
    if "$inkrelay" not in require_string(interface, "default_prompt"):
        raise ValidationError("UI default prompt does not invoke skill")
    readme = read_inside(root, root / "README.md")
    if f"**v{version} · 流程 Skill · MIT**" not in readme:
        raise ValidationError("README version differs from frontmatter")
    if f"git clone --branch v{version}" not in readme:
        raise ValidationError("installation example version differs")
    checked: list[str] = []
    local_links = 0
    documents = sorted(root.rglob("*.md"))
    for document in documents:
        if ".git" in document.relative_to(root).parts:
            continue
        contents = read_inside(root, document)
        local_text = re.sub(r"https?://[^\s)]+", "", contents)
        if re.search(r"\[TODO:|/00[1-4]/|/root/|/Users/|/codex/", local_text):
            raise ValidationError(f"unfinished or machine-specific content: {document}")
        for destination in re.findall(r"\[[^\]]*\]\(([^\s)]+)\)", contents):
            parsed = urlsplit(destination)
            if parsed.scheme or parsed.netloc:
                continue
            if parsed.query:
                raise ValidationError(f"unsupported local query link: {destination}")
            target = (document.parent / unquote(parsed.path)).resolve() if parsed.path else document
            if not target.is_relative_to(root) or not target.exists():
                raise ValidationError(f"missing or escaping local link: {document}: {destination}")
            if parsed.fragment:
                if target.suffix != ".md" or unquote(parsed.fragment) not in headings(read_inside(root, target)):
                    raise ValidationError(f"missing anchor: {document}: {destination}")
            local_links += 1
        checked.append(str(document.relative_to(root)))
    return {"ok": True, "version": version, "yaml_scope": "repository subset, not general YAML", "documents": checked, "local_links": local_links}


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python3 tests/validate_structure.py REPOSITORY", file=sys.stderr)
        return 2
    try:
        result = validate(Path(sys.argv[1]))
    except (ValidationError, OSError, UnicodeError, ValueError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
