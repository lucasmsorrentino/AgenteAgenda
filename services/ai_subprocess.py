"""Invoke Claude Code in headless mode to get structured JSON responses.

Uses `claude -p <prompt> --output-format json` so the user's Max subscription
quota covers the calls (no API key needed). Each call is stateless — there
is no session carried between invocations.

Cost model: 1 subprocess per `/ia`, `/buscar`, or batch classify. The batch
classifier bundles many items into a single prompt, so N items = 1 call.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil

from loguru import logger

CLAUDE_BIN = shutil.which("claude") or "claude"
DEFAULT_TIMEOUT = 90.0  # seconds


class AISubprocessError(RuntimeError):
    """Raised when the subprocess fails or returns unusable output."""


def _extract_json(text: str) -> dict | list:
    """Pull the first valid JSON object/array out of `text`.

    Claude sometimes wraps JSON in ```json fences or adds a prose preamble.
    We scan for the first `{` or `[`, then balance-match to find the end.
    """
    text = text.strip()
    # Strip code fences if present
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()

    start_idx = -1
    for i, ch in enumerate(text):
        if ch in "{[":
            start_idx = i
            break
    if start_idx < 0:
        raise AISubprocessError(f"No JSON found in output: {text[:200]!r}")

    opener = text[start_idx]
    closer = "}" if opener == "{" else "]"
    depth = 0
    in_str = False
    esc = False
    for i in range(start_idx, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                blob = text[start_idx : i + 1]
                try:
                    return json.loads(blob)
                except json.JSONDecodeError as e:
                    raise AISubprocessError(f"Invalid JSON: {e} — {blob[:200]!r}")
    raise AISubprocessError(f"Unterminated JSON in output: {text[:200]!r}")


async def run_claude(prompt: str, timeout: float = DEFAULT_TIMEOUT) -> dict | list:
    """Run `claude -p` with the given prompt and return parsed JSON.

    The prompt should instruct Claude to reply with JSON only — we strip
    fences and prose, but malformed output raises AISubprocessError.
    """
    cmd = [CLAUDE_BIN, "-p", prompt, "--output-format", "json"]
    logger.debug("Invoking Claude subprocess (prompt {} chars)", len(prompt))

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        raise AISubprocessError(
            f"Claude CLI not found at '{CLAUDE_BIN}'. Install Claude Code and ensure `claude` is in PATH."
        )

    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise AISubprocessError(f"Claude subprocess timed out after {timeout}s")

    if proc.returncode != 0:
        err = stderr_b.decode("utf-8", errors="replace")[:400]
        raise AISubprocessError(f"Claude exited {proc.returncode}: {err}")

    raw = stdout_b.decode("utf-8", errors="replace")

    # `--output-format json` wraps the actual text response inside an envelope
    # like {"type": "result", "result": "<assistant text>"}. Unwrap if present.
    try:
        envelope = json.loads(raw)
        if isinstance(envelope, dict) and "result" in envelope:
            raw = envelope["result"]
    except json.JSONDecodeError:
        pass  # not enveloped — treat raw as the assistant text

    return _extract_json(raw)
