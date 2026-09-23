# Security

**Buildspace is a personal tool. It is not designed to be exposed to the
public internet or to any network you do not control.**

Run it on your own machine, bound to loopback, or on a private network
that already authenticates its members, such as a Tailscale tailnet, a
VPN, or an SSH tunnel. Do not put it on a public IP, behind a public
reverse proxy, or on shared Wi-Fi.

## Why

- There is no authentication and no user model. Anyone who can reach the
  port can read the whole timeline, push content, delete entries, and
  clear everything.
- `POST /api/code` and `POST /api/video` take a filesystem path and the
  server reads that file. The path is confined to the roots in
  `BUILDSPACE_ROOTS` (see below), but within those roots whoever can reach
  the API can read any file the server process can read, and the timeline
  is meant to hold source code in the first place.
- Pushed content is stored unencrypted in a SQLite file in your home
  directory and served to any browser that connects.
- Markdown notes are rendered client-side, and the resulting HTML is
  passed through DOMPurify before it reaches the page, so a note that
  quotes hostile HTML cannot run script in the viewer. The frontend's CDN
  libraries are pinned with Subresource Integrity hashes, so a tampered
  CDN file is refused rather than executed.

## What the server does defend against

The one threat that applies even on a single machine is other web pages
open in your browser. Same-origin policy does not cover WebSocket
handshakes or body-less POSTs, and DNS rebinding defeats it entirely, so
the server checks for itself:

- The `Host` header must be loopback, an IP literal, the machine's own
  hostname, or a name listed in `BUILDSPACE_ALLOWED_HOSTS`.
- A browser `Origin` on a WebSocket handshake or on any mutating request
  must pass the same test.

That keeps a random website from subscribing to your pushes or wiping
your timeline. It does nothing against a person on the same network,
which is why the network has to be one you trust.

### Path confinement

`POST /api/code`, `POST /api/diff` and `POST /api/video` name a file for
the server to read, so an unauthenticated caller would otherwise have the
run of the filesystem. Two limits apply to every such path, and to the
re-read that happens when `/media/{id}` streams a video:

- The path must resolve inside one of the allowed roots, set with
  `BUILDSPACE_ROOTS` as a colon-separated list, for example
  `BUILDSPACE_ROOTS=$HOME/Projects:$HOME/Renders`. When it is unset, the
  only root is the directory the server was started from, and that
  fallback is dropped entirely if it is your home directory or `/` (as it
  is under a bare launchd job). The home directory is never an implicit
  root: it holds shell history, agent transcripts, browser profiles and
  mail. Resolution happens before the check, so `..` segments and
  symlinks that point outside a root are rejected rather than followed.
- Credential and personal-data locations are refused even inside a root:
  `.ssh`, `.aws`, `.gnupg`, `.kube`, `.docker`, `.azure`, `.netrc`,
  `.npmrc`, `.pypirc`, `.git-credentials`, `.password-store`, keychains,
  `.config/gh`, `.config/gcloud`, `.config/rclone`, agent state such as
  `.claude`, shell and REPL history files, Buildspace's own
  `.buildspace` database, `Library/Mail`, `Library/Messages`,
  `Library/Cookies`, `Library/Safari`, `Library/Application Support` and
  app containers, any `.env`, anything named like a private key or
  `credentials`, and files ending `.pem`, `.key`, `.p12`, `.pfx`, `.kdbx`,
  `.jks` or `.keystore`. This list is a backstop, not the boundary; the
  roots are.
- Files larger than `BUILDSPACE_MAX_READ_BYTES` (default 5 MB) are refused
  for code and diff pushes, and directories or devices are refused
  outright.

A path outside the roots or on the deny list returns 403; an oversized
file returns 413. Keep `BUILDSPACE_ROOTS` to the directories you actually
push from.

This confinement limits the damage from an unauthenticated caller. It is
not a sandbox, and it is not a reason to expose the port.

## Reporting

If you find a way past the checks above from another origin, or a way to
read a file outside `BUILDSPACE_ROOTS`, email
hello@realitycheckpoint.org rather than opening a public issue.
