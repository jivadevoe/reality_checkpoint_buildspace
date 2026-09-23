# Copyright 2026 Reality Checkpoint
# SPDX-License-Identifier: Apache-2.0
import os
from pathlib import Path


EXT_TO_LANG = {
    ".py": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".jsx": "javascript",
    ".json": "json",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "scss",
    ".md": "markdown",
    ".sh": "bash",
    ".zsh": "bash",
    ".bash": "bash",
    ".rb": "ruby",
    ".go": "go",
    ".rs": "rust",
    ".swift": "swift",
    ".java": "java",
    ".kt": "kotlin",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".m": "objectivec",
    ".mm": "objectivec",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".sql": "sql",
    ".xml": "xml",
    ".plist": "xml",
}


def guess_language(filename: str | None) -> str | None:
    if not filename:
        return None
    ext = Path(filename).suffix.lower()
    return EXT_TO_LANG.get(ext)


DEFAULT_MAX_READ_BYTES = 5 * 1024 * 1024


def max_read_bytes() -> int:
    try:
        return int(os.environ.get("BUILDSPACE_MAX_READ_BYTES", DEFAULT_MAX_READ_BYTES))
    except ValueError:
        return DEFAULT_MAX_READ_BYTES


def read_file(path: str) -> tuple[str, str]:
    p = resolve_readable(path)
    if not p.exists():
        raise FileNotFoundError(f"file not found: {p}")
    if not p.is_file():
        raise NotAFile(f"not a regular file: {p}")
    limit = max_read_bytes()
    size = p.stat().st_size
    if size > limit:
        raise FileTooLarge(
            f"file is {size} bytes, over the {limit}-byte limit "
            "(BUILDSPACE_MAX_READ_BYTES). Push a smaller excerpt as content instead."
        )
    text = p.read_text(encoding="utf-8", errors="replace")
    return text, p.name


# ---------------------------------------------------------------------------
# Path confinement.
#
# /api/code, /api/diff and /api/video all take a filesystem path and the server
# reads it. Without a guard that is an arbitrary file read for anyone who can
# reach the port: /etc/passwd, ~/.ssh/known_hosts, ~/.aws/credentials.
#
# Reads are confined to BUILDSPACE_ROOTS (os.pathsep-separated). When that is
# unset the only root is the directory the server was started from, and never
# implicitly the home directory or the filesystem root: a home-wide default
# exposes shell history, agent transcripts, browser profiles and mail, and no
# deny list can enumerate all of those. A deny list still blocks the usual
# secret stores as a backstop, even inside an explicit root. Paths are fully
# resolved first, so .. traversal and symlinks out of a root are both caught.
# ---------------------------------------------------------------------------

class PathNotAllowed(Exception):
    """Raised when a requested path is outside the allowed roots or denied."""


class FileTooLarge(Exception):
    """Raised when a pushed file exceeds BUILDSPACE_MAX_READ_BYTES."""


class NotAFile(Exception):
    """Raised when a pushed path is a directory, device or other non-file."""


# Any path component matching one of these (case-insensitive) is refused.
_DENY_NAMES = {
    # credentials and keys
    ".ssh", ".aws", ".gnupg", ".gpg", ".kube", ".docker", ".netrc", ".npmrc",
    ".pypirc", ".git-credentials", "keychains", "login.keychain",
    "login.keychain-db", ".password-store", ".authinfo", ".pgpass", ".azure",
    ".terraform.d", ".vault-token", ".cargo", ".gem", ".m2", ".gradle",
    # agent state: transcripts, settings, tokens
    ".claude", ".codex", ".hermes", ".ollama",
    # shell and REPL history
    ".zsh_history", ".bash_history", ".python_history", ".node_repl_history",
    ".psql_history", ".mysql_history", ".sqlite_history", ".lesshst", ".viminfo",
    ".zsh_sessions",
    # Buildspace's own database
    ".buildspace",
}
# Multi-component paths refused wherever they appear.
_DENY_SUBPATHS = (
    ".config/gh", ".config/gcloud", ".config/rclone", ".config/op",
    ".config/hub", ".config/git/credentials", ".local/share/keyrings",
    ".mozilla", ".config/google-chrome", ".config/chromium",
    # macOS personal data (browser profiles, mail, messages, app sandboxes)
    "library/mail", "library/messages", "library/cookies", "library/safari",
    "library/application support", "library/containers",
    "library/group containers", "library/mobile documents",
)
_DENY_SUFFIXES = {
    ".pem", ".key", ".p12", ".pfx", ".keychain", ".keychain-db", ".kdbx",
    ".ovpn", ".mobileprovision", ".jks", ".keystore", ".photoslibrary",
}
_DENY_STEMS = (
    "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa", "credentials", ".env",
    ".htpasswd",
)

def allowed_roots() -> list[Path]:
    raw = os.environ.get("BUILDSPACE_ROOTS", "")
    explicit = bool(raw.strip())
    parts = [p for p in raw.split(os.pathsep) if p.strip()] if explicit else [os.getcwd()]
    out = []
    for p in parts:
        try:
            out.append(Path(p).expanduser().resolve())
        except OSError:
            continue
    if not explicit:
        # The fallback is for "run it from your project directory". Started
        # from / (launchd's default) or from $HOME, it would be far too wide.
        try:
            home = Path.home().resolve()
        except (OSError, RuntimeError):
            home = None
        out = [r for r in out if r != Path(r.anchor) and r != home]
    return out


def _denied(p: Path) -> bool:
    parts = {x.lower() for x in p.parts}
    if parts & _DENY_NAMES:
        return True
    low = p.as_posix().lower()
    if any(f"/{n}/" in low or low.endswith(f"/{n}") for n in _DENY_SUBPATHS):
        return True
    if p.suffix.lower() in _DENY_SUFFIXES:
        return True
    name = p.name.lower()
    if name.startswith(_DENY_STEMS) or name == ".env" or name.startswith(".env."):
        return True
    return False


def resolve_readable(path: str) -> Path:
    """Resolve `path` and confirm it is inside an allowed root and not denied."""
    try:
        p = Path(path).expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        raise PathNotAllowed(f"cannot resolve path: {path}") from exc
    roots = allowed_roots()
    if not roots:
        raise PathNotAllowed(
            "no readable roots are configured. Set BUILDSPACE_ROOTS to the "
            "directories you push files from, or push content instead of a path."
        )
    if not any(p == r or r in p.parents for r in roots):
        raise PathNotAllowed(
            f"path is outside the allowed roots ({', '.join(str(r) for r in roots)}). "
            "Set BUILDSPACE_ROOTS to widen it."
        )
    if _denied(p):
        raise PathNotAllowed("path matches the denied-secrets list and will not be read")
    return p
