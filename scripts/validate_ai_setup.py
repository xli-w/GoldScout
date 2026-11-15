"""Validate repository AI discovery metadata, links, tool limits, and hook/CI syntax."""

import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote

import yaml

ROOT = Path(__file__).resolve().parents[1]


def validate(root=ROOT):
    errors = []
    agents = {}
    documents = {}
    for path in sorted((root / ".github").rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        metadata = {}
        if path.name != "copilot-instructions.md":
            if not text.startswith("---\n") or "\n---\n" not in text[4:]:
                errors.append(f"{path.relative_to(root)}: missing YAML frontmatter")
                continue
            try:
                metadata = yaml.safe_load(text.split("---", 2)[1])
            except yaml.YAMLError:
                errors.append(f"{path.relative_to(root)}: invalid YAML")
                continue
            if not isinstance(metadata, dict) or not isinstance(metadata.get("description"), str):
                errors.append(f"{path.relative_to(root)}: missing description")
                continue
            if path.name == "SKILL.md" and metadata.get("name") != path.parent.name:
                errors.append(f"{path.relative_to(root)}: skill name differs from directory")
            if path.name.endswith(".instructions.md"):
                patterns = metadata.get("applyTo", "")
                if not isinstance(patterns, str) or not patterns or patterns == "**":
                    errors.append(f"{path.relative_to(root)}: instructions need targeted applyTo")
            if path.name.endswith(".agent.md"):
                agents[metadata.get("name", path.stem)] = metadata
        documents[path] = metadata
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
            if "://" not in target and not target.startswith("#"):
                destination = (path.parent / unquote(target.split("#", 1)[0])).resolve()
                if not destination.is_relative_to(root.resolve()) or not destination.exists():
                    errors.append(f"{path.relative_to(root)}: broken/outside link {target}")
    if agents.get("Safety Reviewer", {}).get("tools") != ["read", "search"]:
        errors.append("Safety Reviewer must have read/search tools only")
    if agents.get("Safety Reviewer", {}).get("agents") != []:
        errors.append("Safety Reviewer must not delegate to execution-capable agents")
    if "Developer" not in agents:
        errors.append("Developer agent is missing")
    for path, metadata in documents.items():
        if path.name.endswith(".prompt.md") and metadata.get("agent") not in agents:
            errors.append(f"{path.relative_to(root)}: unknown prompt agent")
        for handoff in metadata.get("handoffs", []):
            if handoff.get("agent") not in agents:
                errors.append(f"{path.relative_to(root)}: unknown handoff agent")
    try:
        hooks = json.loads((root / ".github/hooks/safety.json").read_text(encoding="utf-8"))
        commands = hooks["hooks"]["PreToolUse"]
        expected = "powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File scripts/agent_guard.ps1"
        if not commands or any(command.get("type") != "command" or command.get("windows") != expected for command in commands) or not (root / "scripts/agent_guard.ps1").is_file():
            errors.append("Safety hook must invoke the existing agent_guard.ps1 script")
    except (OSError, ValueError, KeyError, TypeError):
        errors.append("Missing or invalid safety hook JSON")
    try:
        workflow = yaml.safe_load((root / ".github/workflows/offline-validation.yml").read_text(encoding="utf-8"))
        job = workflow["jobs"]["offline"]
        steps = job["steps"]
        if not any(step.get("run", "").strip() == "python scripts/offline_tests.py" for step in steps):
            errors.append("CI must execute the isolated offline runner")
        elif "if" in job or job.get("continue-on-error", False) or not any(
            step.get("run", "").strip() == "python scripts/offline_tests.py"
            and "if" not in step and not step.get("continue-on-error", False)
            for step in steps
        ):
            errors.append("CI regression execution must be unconditional and failure-gating")
    except (OSError, yaml.YAMLError, KeyError, TypeError):
        errors.append("Missing or invalid offline workflow")
    return errors


def main():
    errors = validate()
    for error in errors:
        print(error, file=sys.stderr)
    if not errors:
        print("AI setup: metadata, relative links, agent tool boundaries, hooks, and CI validated.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())