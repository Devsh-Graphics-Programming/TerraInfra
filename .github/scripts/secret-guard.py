#!/usr/bin/env python3
import argparse
import os
import pathlib
import re
import subprocess
import sys


ASSIGNMENT_RE = re.compile(
    r"(?i)\b([A-Z0-9_.-]*(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|client[_-]?secret|webhook|dsn)[A-Z0-9_.-]*)\b\s*[:=]\s*([^#\s]+)"
)
PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
HUNK_RE = re.compile(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def run_git(args):
    return subprocess.run(
        ["git", *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def git_root():
    result = run_git(["rev-parse", "--show-toplevel"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return result.stdout.strip()


def git_ref_exists(ref):
    if not ref or set(ref) == {"0"}:
        return False
    result = run_git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"])
    return result.returncode == 0


def empty_tree():
    result = run_git(["hash-object", "-t", "tree", "/dev/null"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return result.stdout.strip()


def normalize_value(raw):
    return raw.strip().strip("'\"")


def is_allowed_secret_value(value):
    value = normalize_value(value)
    if not value:
        return True
    lowered = value.lower()
    allowed_prefixes = (
        "${{",
        "$",
        "enc[",
        "re.compile(",
        "<",
        "!",
        "*",
    )
    if lowered.startswith(allowed_prefixes):
        return True
    allowed_literals = {
        "true",
        "false",
        "null",
        "none",
        "empty",
        "redacted",
        "masked",
        "example",
        "changeme",
        "placeholder",
        "dummy",
        "local",
        "prod",
        "test",
    }
    if lowered in allowed_literals:
        return True
    if "secrets." in lowered or "<redacted" in lowered:
        return True
    if lowered.startswith("http://127.0.0.1") or lowered.startswith("https://127.0.0.1"):
        return True
    return False


def scan_diff(base, head):
    if not git_ref_exists(base):
        base = empty_tree()

    if head == "WORKTREE":
        diff_args = ["diff", "--unified=0", "--no-ext-diff", base, "--"]
    else:
        diff_args = ["diff", "--unified=0", "--no-ext-diff", base, head, "--"]

    diff_args.extend([":!terraform/provider/**"])
    result = run_git(diff_args)
    if result.returncode not in (0, 1):
        raise RuntimeError(result.stderr.strip())

    findings = []
    current_file = None
    current_line = None
    for raw_line in result.stdout.splitlines():
        if raw_line.startswith("+++ b/"):
            current_file = raw_line[6:]
            current_line = None
            continue
        if raw_line.startswith("@@ "):
            match = HUNK_RE.search(raw_line)
            current_line = int(match.group(1)) if match else None
            continue
        if raw_line.startswith("--- ") or raw_line.startswith("diff ") or raw_line.startswith("index "):
            continue
        if not raw_line.startswith("+"):
            if current_line is not None and not raw_line.startswith("-"):
                current_line += 1
            continue

        added = raw_line[1:]
        line_number = current_line or 0
        if current_line is not None:
            current_line += 1

        if PRIVATE_KEY_RE.search(added):
            findings.append((current_file, line_number, "private-key-block"))
            continue

        assignment = ASSIGNMENT_RE.search(added)
        if not assignment:
            continue

        name = assignment.group(1)
        value = assignment.group(2)
        if is_allowed_secret_value(value):
            continue
        if len(normalize_value(value)) < 8:
            continue

        findings.append((current_file, line_number, name))

    return findings


def tracked_files():
    result = run_git(["ls-files"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return [pathlib.Path(line) for line in result.stdout.splitlines() if line]


def is_sops_secret_path(path):
    parts = set(path.parts)
    if "secrets" not in parts:
        return False
    if path.name == "kustomization.yaml":
        return False
    return path.suffix.lower() in (".yaml", ".yml")


def scan_sops_secret_file(path):
    text = path.read_text(encoding="utf-8")
    if "kind: Secret" not in text:
        return []

    findings = []
    if "\nsops:" not in text and not text.startswith("sops:"):
        findings.append((str(path), 0, "Secret file is missing SOPS metadata"))

    section = None
    section_indent = None
    for line_number, line in enumerate(text.splitlines(), start=1):
        section_match = re.match(r"^(\s*)(data|stringData):\s*$", line)
        if section_match:
            section = section_match.group(2)
            section_indent = len(section_match.group(1))
            continue

        if section is None:
            continue

        if not line.strip() or line.lstrip().startswith("#"):
            continue

        indent = len(line) - len(line.lstrip(" "))
        if indent <= section_indent:
            section = None
            section_indent = None
            continue

        item = re.match(r"^\s*([A-Za-z0-9_.-]+):\s*(.*)$", line)
        if not item:
            continue

        key = item.group(1)
        value = item.group(2).strip()
        if not value or value in ("|", ">") or not value.startswith("ENC["):
            findings.append((str(path), line_number, f"{section}.{key} is not SOPS encrypted"))

    return findings


def scan_sops_secret_files():
    findings = []
    for path in tracked_files():
        if is_sops_secret_path(path):
            findings.extend(scan_sops_secret_file(path))
    return findings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=os.environ.get("SECRET_SCAN_BASE", "HEAD"))
    parser.add_argument("--head", default=os.environ.get("SECRET_SCAN_HEAD", "HEAD"))
    args = parser.parse_args()

    os.chdir(git_root())

    findings = []
    findings.extend(scan_sops_secret_files())
    findings.extend(scan_diff(args.base, args.head))

    if findings:
        print("Secret hygiene check failed. Values are intentionally not printed.", file=sys.stderr)
        for file_name, line_number, reason in findings:
            location = f"{file_name}:{line_number}" if line_number else file_name
            print(f"- {location}: {reason}", file=sys.stderr)
        return 1

    print("Secret hygiene check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
