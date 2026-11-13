"""Local review desk for the text adjudication queue.

A single stdlib HTTP server serves one self-contained page and a tiny JSON API.
The page shows one issue at a time: either a terminology conflict or an OCR
block that needs a human decision.  Decisions go to the data layer
(``tools.review``) when it is available; during UI-only development the server
falls back to ``tests/fixtures/review_queue.json`` so the page can be reviewed
without the data layer, and keeps those demo decisions in memory.

    python -m tools.review_app [--port 8767] [--open] [--force-fixture]
    python -m tools.cli review              # wired by the CLI integration

Routes:

    GET  /                 single-page HTML/CSS/JS, no external assets
    GET  /api/queue        {issues, decisions, stats, source}
    POST /api/decisions    {issue_id, decision, payload, note}
    POST /api/export       run review.export(), return its result
    GET  /api/crop         PNG crop: notice, image, block, scale, context
    GET  /api/notice       one edition Markdown file as text/plain
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import io
import json
import re
import sys
import threading
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from . import archive, crop

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "review_queue.json"

PAGE_TITLE = "文本裁决台"
HOST = "127.0.0.1"
DEFAULT_PORT = 8767
MAX_BODY = 1_000_000
MAX_NOTE = 20_000

_NATIVE_ID = re.compile(r"^[0-9A-Za-z_-]+$")

Record = dict[str, Any]


class BadRequest(ValueError):
    """A request the server can explain in one sentence (HTTP 400)."""


class ReviewUnavailable(RuntimeError):
    """A capability that needs the data layer or Pillow (HTTP 501)."""


@dataclass(frozen=True)
class CropSpec:
    """One validated crop request; the feed id is normalised."""

    notice: str
    image: int
    block: int
    scale: float = 2.0
    context: int = 0


# ---------------------------------------------------------------------------
# Queue assembly
# ---------------------------------------------------------------------------


def import_backend():
    """``tools.review`` when the data layer exists, else ``None``."""
    try:
        return importlib.import_module("tools.review")
    except ModuleNotFoundError as exc:
        if exc.name == "tools.review":
            return None
        raise


def _decided_key(record: Record) -> str:
    value = record.get("decided_at")
    return value if isinstance(value, str) else ""


def normalize_decisions(raw: Any) -> dict[str, Record]:
    """Latest decision per issue id, from a mapping or an append-only list."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        if isinstance(raw.get("decisions"), list):
            raw = raw["decisions"]
        else:
            latest: dict[str, Record] = {}
            for issue_id, value in raw.items():
                if not isinstance(value, dict):
                    continue
                record = dict(value)
                record.setdefault("issue_id", issue_id)
                previous = latest.get(str(issue_id))
                if previous is None or _decided_key(record) >= _decided_key(previous):
                    latest[str(issue_id)] = record
            return latest
    if isinstance(raw, list):
        latest = {}
        for value in raw:
            if not isinstance(value, dict):
                continue
            issue_id = value.get("issue_id")
            if not isinstance(issue_id, str) or not issue_id:
                continue
            record = dict(value)
            previous = latest.get(issue_id)
            if previous is None or _decided_key(record) >= _decided_key(previous):
                latest[issue_id] = record
        return latest
    return {}


def issue_status(issue: Record, decisions: dict[str, Record]) -> str:
    record = decisions.get(str(issue.get("id")))
    if record is None:
        return "pending"
    if record.get("decision") == "defer":
        return "deferred"
    return "decided"


def decorate_issues(issues: Any, decisions: dict[str, Record]) -> list[Record]:
    """Copy issues with ``status``/``decision`` and queue order applied.

    Pending issues keep the data layer's order (high severity and impact for
    terms, source damage first for OCR); decided issues follow, most recently
    decided first.
    """
    pending: list[Record] = []
    decided: list[Record] = []
    if not isinstance(issues, list):
        return []
    order_by_id: dict[str, int] = {}
    for position, raw in enumerate(issues):
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        issue = dict(raw)
        issue["id"] = str(issue["id"])
        issue["title"] = str(issue.get("title") or issue["id"])
        order_by_id[issue["id"]] = position
        status = issue_status(issue, decisions)
        issue["status"] = status
        issue["decision"] = decisions.get(issue["id"])
        (decided if status == "decided" else pending).append(issue)

    def rank(issue: Record) -> tuple[int, int]:
        return (
            0 if issue.get("status") == "pending" else 1,
            order_by_id.get(issue["id"], 0),
        )

    pending.sort(key=rank)
    decided.sort(key=lambda issue: _decided_key(issue.get("decision") or {}), reverse=True)
    return pending + decided


def compute_stats(issues: list[Record]) -> Record:
    total = len(issues)
    pending = sum(1 for issue in issues if issue.get("status") in {"pending", "deferred"})
    deferred = sum(1 for issue in issues if issue.get("status") == "deferred")
    decided = sum(1 for issue in issues if issue.get("status") == "decided")
    high = sum(
        1
        for issue in issues
        if issue.get("status") in {"pending", "deferred"} and issue.get("severity") == "high"
    )
    return {
        "total": total,
        "pending": pending,
        "deferred": deferred,
        "decided": decided,
        "high": high,
        "progress": (decided / total) if total else 0.0,
    }


def filter_issues(issues: list[Record], name: str) -> list[Record]:
    """The queue filters the page offers; unknown names mean ``all``."""
    name = (name or "all").strip()
    if name in {"term", "term_conflict"}:
        return [issue for issue in issues if issue.get("type") == "term_conflict"]
    if name in {"ocr", "ocr_block"}:
        return [issue for issue in issues if issue.get("type") == "ocr_block"]
    if name == "high":
        return [
            issue
            for issue in issues
            if issue.get("status") in {"pending", "deferred"}
            and issue.get("severity") == "high"
        ]
    if name == "pending":
        return [issue for issue in issues if issue.get("status") in {"pending", "deferred"}]
    if name == "decided":
        return [issue for issue in issues if issue.get("status") == "decided"]
    if name == "deferred":
        return [issue for issue in issues if issue.get("status") == "deferred"]
    return list(issues)


def load_fixture(path: str | Path = FIXTURE_PATH) -> tuple[list[Record], list[Record]]:
    """Issues and demo decisions from the fixture (or ``issues`` list form)."""
    target = Path(path)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ReviewUnavailable(f"读不到示例队列：{target}") from exc
    except json.JSONDecodeError as exc:
        raise ReviewUnavailable(f"示例队列不是合法 JSON：{target}: {exc}") from exc
    if isinstance(raw, list):
        return raw, []
    if isinstance(raw, dict):
        issues = raw.get("issues", [])
        decisions = raw.get("decisions", [])
        if isinstance(issues, list):
            return issues, decisions if isinstance(decisions, list) else []
    raise ReviewUnavailable(f"示例队列格式不对：{target}")


def jsonable(value: Any) -> Any:
    """A JSON-safe copy: paths become posix strings, odd values become text."""
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class ReviewStore:
    """Queue and decisions, backed by ``tools.review`` or the fixture."""

    def __init__(
        self,
        *,
        force_fixture: bool = False,
        fixture_path: str | Path = FIXTURE_PATH,
        backend: Any = None,
    ) -> None:
        self.fixture_path = Path(fixture_path)
        if force_fixture:
            self.backend = None
        elif backend is not None:
            self.backend = backend
        else:
            self.backend = import_backend()
        self.source = "review" if self.backend is not None else "fixture"
        self._memory: dict[str, Record] = {}
        self._lock = threading.Lock()

    def _load(self) -> tuple[list[Record], dict[str, Record]]:
        if self.backend is not None:
            built = self.backend.build_queue()
            embedded: Any = None
            if isinstance(built, dict):
                issues = built.get("issues", [])
                embedded = built.get("decisions")
            else:
                issues = built
            raw_decisions = self.backend.load_decisions()
            if raw_decisions in (None, [], {}) and embedded is not None:
                raw_decisions = embedded
            return list(issues or []), normalize_decisions(raw_decisions)

        issues, fixture_decisions = load_fixture(self.fixture_path)
        decisions = normalize_decisions(fixture_decisions)
        with self._lock:
            decisions.update(self._memory)
        return issues, decisions

    def queue(self) -> Record:
        issues, decisions = self._load()
        decorated = decorate_issues(issues, decisions)
        return {
            "issues": decorated,
            "decisions": decisions,
            "stats": compute_stats(decorated),
            "source": self.source,
        }

    def _find_issue(self, issue_id: str) -> Record | None:
        issues, _ = self._load()
        for issue in issues:
            if isinstance(issue, dict) and str(issue.get("id")) == issue_id:
                return issue
        return None

    def save_decision(
        self,
        issue_id: str,
        decision: str,
        payload: Record | None = None,
        note: str = "",
    ) -> Any:
        payload = dict(payload or {})
        issue = self._find_issue(issue_id)
        if issue is None:
            raise BadRequest(f"未知 issue：{issue_id}")
        options = issue.get("options")
        if isinstance(options, list):
            allowed = {
                str(option.get("value"))
                for option in options
                if isinstance(option, dict) and option.get("value")
            }
            if allowed and decision not in allowed:
                raise BadRequest(f"issue 不支持这个决定：{decision}")
        _validate_payload(issue, decision, payload)

        if self.backend is not None:
            return _backend_save(self.backend, issue_id, decision, payload, note)

        record: Record = {
            "issue_id": issue_id,
            "type": issue.get("type"),
            "decision": decision,
            "note": note,
            "decided_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        for key in ("preferred", "aliases", "corrected"):
            value = payload.get(key)
            if value not in (None, "", []):
                record[key] = value
        with self._lock:
            self._memory[issue_id] = record
        return record

    def export(self) -> Any:
        if self.backend is None:
            raise ReviewUnavailable(
                "数据层 tools/review.py 尚未就绪；示例模式不写入 review/ 目录"
            )
        function = getattr(self.backend, "export", None)
        if not callable(function):
            raise ReviewUnavailable("tools.review 没有 export()")
        return function()


def _validate_payload(issue: Record, decision: str, payload: Record) -> None:
    if decision == "same_alias":
        preferred = payload.get("preferred")
        if not isinstance(preferred, str) or not preferred.strip():
            raise BadRequest("same_alias 需要 payload.preferred")
        variants = {
            str(variant.get("text"))
            for variant in issue.get("variants") or []
            if isinstance(variant, dict) and variant.get("text")
        }
        if variants and preferred not in variants:
            raise BadRequest(f"preferred 不在两种写法里：{preferred}")
        aliases = payload.get("aliases")
        if aliases is not None and not isinstance(aliases, list):
            raise BadRequest("payload.aliases 必须是列表")
    elif decision == "override":
        corrected = payload.get("corrected")
        if not isinstance(corrected, str) or not corrected.strip():
            raise BadRequest("override 需要 payload.corrected")


def _backend_save(
    backend: Any, issue_id: str, decision: str, payload: Record, note: str
) -> Any:
    function = getattr(backend, "save_decision", None)
    if not callable(function):
        raise ReviewUnavailable("tools.review 没有 save_decision()")
    try:
        parameters = inspect.signature(function).parameters
    except (TypeError, ValueError):
        parameters = {}
    var_keyword = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    kwargs: Record = {}
    if "issue_id" in parameters or var_keyword:
        kwargs["issue_id"] = issue_id
    if "decision" in parameters or var_keyword:
        kwargs["decision"] = decision
    if "payload" in parameters or var_keyword:
        kwargs["payload"] = payload
    else:
        for key in ("preferred", "aliases", "corrected"):
            if key in parameters and key in payload:
                kwargs[key] = payload[key]
    if "note" in parameters or var_keyword:
        kwargs["note"] = note
    if not kwargs:
        return function(issue_id, decision, payload, note)
    return function(**kwargs)


# ---------------------------------------------------------------------------
# Crop and notice reads
# ---------------------------------------------------------------------------


def _one_param(params: dict[str, list[str]], key: str, required: bool = True) -> str | None:
    values = params.get(key)
    if not values or not values[0].strip():
        if required:
            raise BadRequest(f"缺少参数 {key}")
        return None
    return values[0].strip()


def _feed_from_notice(value: str) -> str:
    source, native = archive.split_id(value)
    if source != "dashen":
        raise BadRequest("crop 只支持 dashen 公告")
    if not _NATIVE_ID.fullmatch(native):
        raise BadRequest(f"公告 id 不合法：{value}")
    return native


def parse_crop_params(query: str) -> CropSpec:
    """Validate ``notice/image/block/scale/context``; raise ``BadRequest``."""
    params = parse_qs(query, keep_blank_values=True)
    notice = _feed_from_notice(_one_param(params, "notice") or "")
    try:
        image = int(_one_param(params, "image") or "")
        block = int(_one_param(params, "block") or "")
    except ValueError as exc:
        raise BadRequest("image/block 必须是整数") from exc
    raw_scale = _one_param(params, "scale", required=False)
    raw_context = _one_param(params, "context", required=False)
    try:
        scale = float(raw_scale) if raw_scale is not None else 2.0
        context = int(raw_context) if raw_context is not None else 0
    except ValueError as exc:
        raise BadRequest("scale/context 必须是数字") from exc
    if image < 1:
        raise BadRequest("image 从 1 开始")
    if block < 0:
        raise BadRequest("block 从 0 开始")
    if not 0 <= scale <= crop.MAX_SCALE:
        raise BadRequest(f"scale 必须在 0 到 {crop.MAX_SCALE:g} 之间")
    if not 0 <= context <= 4:
        raise BadRequest("context 必须在 0 到 4 之间")
    return CropSpec(notice=notice, image=image, block=block, scale=scale, context=context)


@lru_cache(maxsize=128)
def _crop_png(notice: str, image: int, block: int, scale: float, context: int) -> bytes:
    feed = _feed_from_notice(notice)
    notice_record = archive.load_ocr(feed)
    images = notice_record.get("images") or []
    if not 1 <= image <= len(images):
        raise BadRequest(f"image 超出范围：{image}（共 {len(images)} 张）")
    image_record = images[image - 1]
    if not isinstance(image_record, dict):
        raise BadRequest(f"image {image} 没有可用记录")
    if image_record.get("error"):
        raise BadRequest(f"image {image} OCR 失败：{image_record['error']}")
    path = image_record.get("path")
    if not path:
        raise BadRequest(f"image {image} 没有本地图片")
    blocks = image_record.get("blocks") or []
    if not 0 <= block < len(blocks):
        raise BadRequest(f"block 超出范围：{block}（共 {len(blocks)} 个）")

    try:
        box = crop.crop_box(blocks, block, context=context, pad_x=40, pad_y=12)
        rendered = crop.render(REPO_ROOT / path, box, scale=scale)
    except ImportError as exc:
        raise ReviewUnavailable(
            "Pillow 未安装，无法渲染 crop（安装 ocr extra 后重试）"
        ) from exc
    except IndexError as exc:
        raise BadRequest(str(exc)) from exc

    buffer = io.BytesIO()
    rendered.save(buffer, format="PNG", optimize=False)
    return buffer.getvalue()


def crop_png(spec: CropSpec) -> bytes:
    return _crop_png(spec.notice, spec.image, spec.block, spec.scale, spec.context)


def notice_path(notice_id: str, root: str | Path | None = None) -> Path:
    """``dashen.<feed>`` / ``nga.<id>`` to its edition Markdown file."""
    if not isinstance(notice_id, str) or not notice_id.strip():
        raise BadRequest("缺少参数 id")
    source_name, native = archive.split_id(notice_id.strip())
    source = archive.SOURCES.get(source_name)
    if source is None or not _NATIVE_ID.fullmatch(native):
        raise BadRequest(f"公告 id 不合法：{notice_id}")
    base = Path(root) if root else REPO_ROOT
    return base / source.edition_dir / f"{native}.md"


def read_notice(notice_id: str, root: str | Path | None = None) -> str:
    path = notice_path(notice_id, root)
    if not path.is_file():
        raise FileNotFoundError(f"没有这篇 edition：{notice_id}")
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


class ReviewHandler(BaseHTTPRequestHandler):
    server_version = "ReviewDesk/1.0"
    server: "ReviewHTTPServer"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Quiet by default: one local reviewer does not need an access log."""

    def do_GET(self) -> None:  # noqa: N802
        self._guard(self._get)

    def do_POST(self) -> None:  # noqa: N802
        self._guard(self._post)

    def _guard(self, action) -> None:
        try:
            action()
        except BadRequest as exc:
            self._json(400, {"error": str(exc)})
        except FileNotFoundError as exc:
            self._json(404, {"error": str(exc) or "文件不存在"})
        except ReviewUnavailable as exc:
            self._json(501, {"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:  # noqa: BLE001 - the server must answer
            print(f"review-app error: {exc!r}", file=sys.stderr)
            self._json(500, {"error": f"内部错误：{exc}"})

    def _get(self) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path
        if path in {"/", "/index.html"}:
            self._send(200, PAGE_HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api/queue":
            self._json(200, self.server.store.queue())
        elif path == "/api/crop":
            spec = parse_crop_params(parsed.query)
            self._send(200, crop_png(spec), "image/png", cache="private, max-age=60")
        elif path == "/api/notice":
            params = parse_qs(parsed.query, keep_blank_values=True)
            notice_id = _one_param(params, "id") or ""
            self._send(200, read_notice(notice_id).encode("utf-8"), "text/plain; charset=utf-8")
        elif path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
        else:
            self._json(404, {"error": f"没有这个路径：{path}"})

    def _post(self) -> None:
        path = urlsplit(self.path).path
        if path == "/api/decisions":
            body = self._read_json()
            issue_id = body.get("issue_id")
            decision = body.get("decision")
            payload = body.get("payload") or {}
            note = body.get("note") or ""
            if not isinstance(issue_id, str) or not issue_id.strip():
                raise BadRequest("缺少 issue_id")
            if not isinstance(decision, str) or not decision.strip():
                raise BadRequest("缺少 decision")
            if not isinstance(payload, dict):
                raise BadRequest("payload 必须是对象")
            if not isinstance(note, str):
                raise BadRequest("note 必须是字符串")
            if len(note) > MAX_NOTE:
                raise BadRequest("note 太长")
            saved = self.server.store.save_decision(
                issue_id.strip(), decision.strip(), payload, note
            )
            self._json(
                200,
                {
                    "ok": True,
                    "decision": jsonable(saved),
                    "queue": self.server.store.queue(),
                },
            )
        elif path == "/api/export":
            self._json(200, {"ok": True, "result": jsonable(self.server.store.export())})
        else:
            self._json(404, {"error": f"没有这个路径：{path}"})

    def _read_json(self) -> Record:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise BadRequest("请求体为空")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise BadRequest("Content-Length 不合法") from exc
        if length <= 0:
            raise BadRequest("请求体为空")
        if length > MAX_BODY:
            raise BadRequest("请求体太大")
        raw = self.rfile.read(length)
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BadRequest(f"请求体不是合法 JSON：{exc}") from exc
        if not isinstance(value, dict):
            raise BadRequest("请求体必须是 JSON 对象")
        return value

    def _json(self, status: int, value: Any) -> None:
        body = json.dumps(jsonable(value), ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        self._send(status, body, "application/json; charset=utf-8")

    def _send(
        self,
        status: int,
        body: bytes,
        content_type: str,
        *,
        cache: str = "no-store",
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if body:
            self.wfile.write(body)


class ReviewHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], store: ReviewStore) -> None:
        self.store = store
        super().__init__(address, ReviewHandler)


def make_server(
    port: int = DEFAULT_PORT,
    host: str = HOST,
    *,
    store: ReviewStore | None = None,
    force_fixture: bool = False,
    fixture_path: str | Path = FIXTURE_PATH,
) -> ReviewHTTPServer:
    """A server ready for ``serve_forever``; tests pass ``port=0``."""
    if store is None:
        store = ReviewStore(force_fixture=force_fixture, fixture_path=fixture_path)
    return ReviewHTTPServer((host, port), store)


def serve(
    port: int = DEFAULT_PORT,
    open_browser: bool = False,
    host: str = HOST,
    *,
    store: ReviewStore | None = None,
    force_fixture: bool = False,
) -> None:
    """Run the desk until Ctrl+C; ``port=0`` picks a free port."""
    server = make_server(port, host, store=store, force_fixture=force_fixture)
    bound_port = server.server_address[1]
    url = f"http://{host}:{bound_port}/"
    print(f"review desk: {url}  ({server.store.source})")
    if open_browser:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
        print("review desk stopped")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="本地文本裁决台")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--open", action="store_true", help="启动后打开浏览器")
    parser.add_argument(
        "--force-fixture",
        action="store_true",
        help="不导入 tools.review，直接用 tests/fixtures/review_queue.json",
    )
    args = parser.parse_args(argv)
    serve(args.port, args.open, args.host, force_fixture=args.force_fixture)
    return 0


PAGE_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>文本裁决台 · HPMA 公告文本复核</title>
<style>
:root {
  --bg: #f5f3ee;
  --paper: #fffdf9;
  --ink: #25231f;
  --muted: #6f6a61;
  --line: #e1dcd2;
  --accent: #6b4f2a;
  --accent-2: #8a6a3f;
  --mark: #ffe9a8;
  --ok: #2f6b4f;
  --warn: #8a4a12;
  --shadow: 0 1px 2px rgba(35, 30, 20, .04), 0 8px 24px rgba(35, 30, 20, .05);
}
* { box-sizing: border-box; }
html, body { margin: 0; }
body {
  background: var(--bg);
  color: var(--ink);
  font: 14px/1.65 system-ui, -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
  -webkit-font-smoothing: antialiased;
}
button, input, textarea, select { font: inherit; color: inherit; }
button { cursor: pointer; }
a { color: var(--accent); }
.top {
  position: sticky; top: 0; z-index: 40;
  background: rgba(245, 243, 238, .95);
  backdrop-filter: blur(12px);
  border-bottom: 1px solid var(--line);
}
.top-inner {
  max-width: 1200px; margin: 0 auto; padding: 14px 22px;
  display: flex; gap: 18px; align-items: center; flex-wrap: wrap;
}
.brand h1 { margin: 0; font-size: 19px; letter-spacing: .01em; font-weight: 700; }
.brand p { margin: 2px 0 0; color: var(--muted); font-size: 12px; }
.stats { display: flex; gap: 18px; align-items: center; margin-left: auto; flex-wrap: wrap; }
.stat { min-width: 62px; }
.stat b { display: block; font-size: 20px; line-height: 1.2; font-variant-numeric: tabular-nums; }
.stat span { color: var(--muted); font-size: 11px; }
.progress { width: 160px; }
.bar { height: 6px; border-radius: 999px; background: #e7e1d5; overflow: hidden; }
.bar i { display: block; height: 100%; width: 0; background: linear-gradient(90deg, #8a6a3f, #6b4f2a); transition: width .25s ease; }
.progress small { display: block; color: var(--muted); font-size: 11px; margin-top: 4px; }
.icon-btn {
  border: 1px solid var(--line); background: var(--paper); border-radius: 999px;
  padding: 7px 13px; font-size: 12px; color: var(--muted);
}
.icon-btn:hover { border-color: var(--accent); color: var(--accent); }
.shell {
  max-width: 1200px; margin: 0 auto; padding: 22px;
  display: grid; grid-template-columns: 288px minmax(0, 1fr); gap: 22px;
  align-items: start;
}
.side {
  position: sticky; top: 92px; max-height: calc(100vh - 112px);
  display: flex; flex-direction: column;
  background: var(--paper); border: 1px solid var(--line);
  border-radius: 14px; box-shadow: var(--shadow); overflow: hidden;
}
.side-head { padding: 12px 13px; border-bottom: 1px solid var(--line); display: flex; gap: 6px; flex-wrap: wrap; }
.chip {
  border: 1px solid var(--line); background: #fff; color: var(--muted);
  border-radius: 999px; padding: 4px 9px; font-size: 11px; line-height: 1.4;
}
.chip:hover { border-color: var(--accent-2); color: var(--accent); }
.chip.on { background: var(--accent); border-color: var(--accent); color: #fff; }
.chip-count { opacity: .65; margin-left: 4px; font-variant-numeric: tabular-nums; }
.queue { overflow: auto; padding: 8px; }
.qitem {
  width: 100%; text-align: left; border: 1px solid transparent;
  background: transparent; border-radius: 10px; padding: 8px 9px; display: block;
}
.qitem:hover { background: #f7f4ed; }
.qitem.current { background: #f1ebdf; border-color: #d9cfbd; }
.qtitle {
  display: block; font-size: 12.5px; line-height: 1.4; font-weight: 600;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.qmeta { display: flex; gap: 6px; align-items: center; margin-top: 3px; font-size: 10.5px; color: var(--muted); }
.dot { width: 6px; height: 6px; border-radius: 50%; background: #c9c2b5; display: inline-block; flex: none; }
.dot.high { background: #b5651d; }
.dot.decided { background: var(--ok); }
.dot.deferred { background: #8a7a55; }
.side-foot { padding: 9px 12px; border-top: 1px solid var(--line); color: var(--muted); font-size: 11px; }
.qempty { padding: 20px 12px; color: var(--muted); font-size: 12px; text-align: center; }
main { min-width: 0; }
.issue {
  background: var(--paper); border: 1px solid var(--line); border-radius: 16px;
  box-shadow: var(--shadow); padding: 26px 30px 24px;
}
.kicker { display: flex; gap: 8px; align-items: center; color: var(--muted); font-size: 11px; letter-spacing: .06em; text-transform: uppercase; }
.tag {
  display: inline-block; border: 1px solid #d8cdb9; border-radius: 999px;
  padding: 2px 8px; font-size: 10.5px; letter-spacing: 0;
  color: var(--accent); background: #fbf7ee;
}
.tag.high { color: var(--warn); border-color: #e8c9a0; background: #fff6ea; }
.tag.decided { color: var(--ok); border-color: #bfd8c9; background: #f2faf5; }
.tag.risk { color: #6b5a3b; border-color: #ddd0b8; background: #fbf7ee; }
.tag.risk.damage { color: #8c2f2f; border-color: #eccaca; background: #fff5f5; }
.issue h2 { margin: 12px 0 6px; font-size: 25px; line-height: 1.3; letter-spacing: .005em; }
.why {
  margin: 14px 0 0; padding: 12px 14px; border-left: 3px solid var(--accent);
  background: #faf7f1; border-radius: 0 8px 8px 0; color: #4d473f; font-size: 13px;
}
.why b { color: var(--accent); font-size: 11px; letter-spacing: .08em; display: block; margin-bottom: 2px; }
.question { margin: 10px 0 0; color: var(--muted); font-size: 13px; }
.reco {
  margin-top: 12px; font-size: 12px; color: var(--muted); background: #fff;
  border: 1px dashed var(--line); border-radius: 9px; padding: 9px 12px;
}
.reco b { color: var(--accent); margin-right: 6px; }
h3.sec { margin: 26px 0 10px; font-size: 11px; letter-spacing: .1em; color: var(--muted); font-weight: 700; text-transform: uppercase; }
.variants { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.variant { border: 1px solid var(--line); background: #fff; border-radius: 12px; padding: 12px 15px; }
.variant.main { border-color: #cbb893; background: #fdf9ef; }
.variant .vflag { float: right; font-size: 10px; color: var(--accent); }
.variant .vtext { font-size: 22px; font-weight: 700; letter-spacing: .03em; }
.variant .vcounts { display: block; color: var(--muted); font-size: 12px; margin-top: 2px; }
.impact { margin-top: 10px; font-size: 12.5px; color: #4d473f; background: #f7f5ef; border-radius: 9px; padding: 9px 12px; }
.impact b { color: var(--accent); }
.match-note { margin: 6px 0 0; color: #8a8478; font-size: 11px; line-height: 1.5; }
.ctx-cols { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.ctx-col { min-width: 0; }
.ctx-col h4 { margin: 0 0 7px; font-size: 12px; font-weight: 600; color: var(--muted); }
.ctx { border: 1px solid var(--line); border-radius: 9px; padding: 8px 10px; margin-bottom: 8px; background: #fff; font-size: 12px; }
.ctx .line { line-height: 1.55; word-break: break-word; }
.ctx .nb { color: #9a9489; font-size: 11px; margin-top: 3px; white-space: pre-wrap; word-break: break-word; }
.ctx .src { display: flex; gap: 8px; align-items: center; color: #9a9489; font-size: 10.5px; margin-bottom: 2px; flex-wrap: wrap; }
.ctx .src a { color: #9a9489; }
.ctx-empty { color: var(--muted); font-size: 12px; padding: 8px 2px; }
mark { background: var(--mark); color: inherit; border-radius: 2px; padding: 0 1px; }
.more { border: 0; background: none; color: var(--accent); font-size: 11.5px; padding: 2px 0; }
.ocr-grid { display: grid; grid-template-columns: minmax(0, 1.05fr) minmax(0, .95fr); gap: 18px; margin-top: 18px; }
.crop { margin: 0; }
.crop img {
  display: block; width: 100%; max-height: 320px; object-fit: contain;
  background: #f0ece3; border: 1px solid var(--line); border-radius: 10px; cursor: zoom-in;
}
.crop figcaption { color: var(--muted); font-size: 11px; margin-top: 6px; }
.crop-missing { color: var(--muted); font-size: 12px; border: 1px dashed var(--line); border-radius: 10px; padding: 24px; text-align: center; }
.ocr-side { border: 1px solid var(--line); border-radius: 12px; padding: 14px 16px; background: #fff; }
.field { margin-bottom: 11px; }
.field:last-child { margin-bottom: 0; }
.field label, .override label, .note label {
  display: block; font-size: 10.5px; letter-spacing: .08em; color: var(--muted); margin-bottom: 3px;
}
.field p { margin: 0; font-size: 13px; line-height: 1.55; word-break: break-word; }
.field .mono {
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 12px; background: #f7f5ef; border-radius: 6px; padding: 6px 8px;
}
.conf { display: inline-block; border-radius: 999px; padding: 1px 8px; font-size: 10.5px; background: #fff6ea; border: 1px solid #e8c9a0; color: var(--warn); }
.ctx-strip { margin-top: 18px; border: 1px dashed var(--line); border-radius: 10px; padding: 11px 14px; background: #fcfaf5; }
.ctx-strip .row { display: grid; grid-template-columns: 64px 1fr; gap: 10px; font-size: 12px; line-height: 1.55; margin: 2px 0; }
.ctx-strip .row span { color: #9a9489; font-size: 10.5px; }
.ctx-strip .row div { white-space: pre-wrap; word-break: break-word; }
.decision { margin-top: 28px; border-top: 1px solid var(--line); padding-top: 20px; }
.opts { display: grid; grid-template-columns: 1fr 1fr; gap: 9px; }
.option {
  display: flex; gap: 10px; align-items: flex-start; text-align: left;
  border: 1px solid var(--line); background: #fff; border-radius: 10px; padding: 10px 12px;
}
.option:hover { border-color: #cbb893; }
.option.sel { border-color: var(--accent); background: #fbf7ee; box-shadow: inset 0 0 0 1px var(--accent); }
.option .key {
  flex: none; width: 20px; height: 20px; border-radius: 5px; background: #f0ece3;
  color: var(--muted); font-size: 11px; display: grid; place-items: center; margin-top: 1px;
}
.option.sel .key { background: var(--accent); color: #fff; }
.option .opt-label { font-weight: 600; font-size: 12.5px; line-height: 1.4; }
.option .opt-desc { display: block; color: var(--muted); font-size: 11px; margin-top: 1px; }
.preferred { margin-top: 10px; padding: 11px 13px; border: 1px solid var(--line); border-radius: 10px; background: #fff; }
.preferred p { margin: 0 0 7px; font-size: 11.5px; color: var(--muted); }
.pref-row { display: flex; gap: 8px; flex-wrap: wrap; }
.pref { border: 1px solid var(--line); background: #fff; border-radius: 999px; padding: 5px 12px; font-size: 12px; }
.pref small { opacity: .65; margin-left: 3px; }
.pref.on { border-color: var(--accent); background: var(--accent); color: #fff; }
textarea {
  width: 100%; min-height: 64px; resize: vertical; border: 1px solid var(--line);
  border-radius: 9px; background: #fff; padding: 9px 11px; font-size: 12.5px; line-height: 1.55;
}
textarea:focus { outline: 2px solid #e8dcc4; border-color: #cbb893; }
.override, .note { margin-top: 11px; }
.savebar { display: flex; gap: 10px; align-items: center; margin-top: 16px; flex-wrap: wrap; }
.btn { border: 1px solid var(--line); background: #fff; border-radius: 9px; padding: 9px 14px; font-size: 13px; }
.btn:hover { border-color: var(--accent); color: var(--accent); }
.btn.primary { background: var(--accent); border-color: var(--accent); color: #fff; font-weight: 600; }
.btn.primary:hover { background: #5b4222; color: #fff; }
.btn.ghost { background: transparent; color: var(--muted); }
.btn:disabled { opacity: .45; cursor: not-allowed; }
.spacer { flex: 1; }
.hint { color: var(--muted); font-size: 11px; }
kbd {
  font-family: inherit; font-size: 10px; border: 1px solid #d8d1c4;
  border-bottom-width: 2px; border-radius: 4px; padding: 0 4px;
  background: #faf8f3; color: var(--muted);
}
.btn.primary kbd { background: rgba(255, 255, 255, .15); border-color: rgba(255, 255, 255, .3); color: #fff; }
.banner {
  display: flex; gap: 10px; align-items: center; justify-content: space-between;
  background: #f2faf5; border: 1px solid #cfe3d7; color: var(--ok);
  border-radius: 10px; padding: 9px 12px; font-size: 12px; margin: 0 0 16px;
}
.banner .btn { padding: 5px 10px; font-size: 11.5px; color: var(--ok); border-color: #cfe3d7; }
.banner span { min-width: 0; }
.empty {
  text-align: center; padding: 70px 20px; background: var(--paper);
  border: 1px dashed var(--line); border-radius: 16px; color: var(--muted);
}
.empty h2 { margin: 0 0 6px; color: var(--ink); font-size: 20px; }
.empty p { margin: 0 auto 16px; max-width: 430px; font-size: 13px; }
.empty .actions { display: flex; gap: 8px; justify-content: center; flex-wrap: wrap; }
.details-json { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 11px; white-space: pre-wrap; background: #f7f5ef; border-radius: 9px; padding: 12px; }
.toast {
  position: fixed; left: 50%; bottom: 26px; transform: translate(-50%, 20px);
  background: #2b2823; color: #fff; border-radius: 10px; padding: 10px 16px;
  font-size: 12.5px; opacity: 0; pointer-events: none; transition: .2s ease;
  z-index: 80; max-width: min(580px, 90vw); box-shadow: 0 8px 30px rgba(0, 0, 0, .2);
}
.toast.show { opacity: 1; transform: translate(-50%, 0); }
.lightbox {
  position: fixed; inset: 0; background: rgba(25, 23, 19, .82);
  display: none; align-items: center; justify-content: center; padding: 24px;
  z-index: 90; cursor: zoom-out;
}
.lightbox.open { display: flex; }
.lightbox img { max-width: 96vw; max-height: 92vh; border-radius: 8px; background: #fff; }
.queue-toggle { display: none; }
@media (max-width: 980px) {
  .shell { grid-template-columns: 1fr; padding: 14px; }
  .shell .side { display: none; position: static; max-height: none; }
  .shell .side.open { display: flex; }
  .queue-toggle { display: inline-flex; }
  .top-inner { padding: 11px 14px; gap: 12px; }
  .stats { margin-left: 0; gap: 14px; }
  .progress { width: 130px; }
  .issue { padding: 20px 18px; }
  .ocr-grid, .ctx-cols, .variants, .opts { grid-template-columns: 1fr; }
  .issue h2 { font-size: 21px; }
  .brand p { display: none; }
}
</style>
</head>
<body>
<header class="top">
  <div class="top-inner">
    <div class="brand">
      <h1>文本裁决台</h1>
      <p>一次只处理一个文本问题</p>
    </div>
    <div class="stats" aria-label="进度">
      <div class="stat"><b id="statPending">–</b><span>待裁决</span></div>
      <div class="stat"><b id="statHigh">–</b><span>高优先</span></div>
      <div class="stat"><b id="statDecided">–</b><span>已裁决</span></div>
      <div class="progress">
        <div class="bar"><i id="barFill"></i></div>
        <small id="progressText">加载中…</small>
      </div>
    </div>
    <button class="icon-btn queue-toggle" data-action="toggle-queue" aria-controls="queuePane" aria-expanded="false">队列</button>
    <button class="icon-btn" data-action="export">导出决定</button>
  </div>
</header>

<div class="shell">
  <aside class="side" id="queuePane" aria-label="裁决队列">
    <div class="side-head" id="filters"></div>
    <div class="queue" id="queueList"></div>
    <div class="side-foot" id="queueFoot">加载中…</div>
  </aside>
  <main id="main">
    <noscript><div class="empty"><h2>需要 JavaScript</h2><p>这个本地页面用内联脚本渲染队列。</p></div></noscript>
  </main>
</div>

<div class="toast" id="toast" role="status" aria-live="polite"></div>
<div class="lightbox" id="lightbox" data-action="close-lightbox" role="dialog" aria-label="放大查看证据图">
  <img id="lightboxImg" alt="OCR block 放大图">
</div>

<script>
(function () {
  "use strict";

  var state = {
    issues: [],
    decisions: {},
    stats: {},
    source: "",
    filter: "term",
    currentId: null,
    selected: null,
    preferred: null,
    overrideText: "",
    note: "",
    contextExpanded: {},
    history: [],
    busy: false
  };

  var TYPE_LABEL = { term_conflict: "术语", ocr_block: "OCR" };
  var SEV_LABEL = { high: "高优先", medium: "中", low: "低" };
  var RISK_LABEL = { source_damage: "源图损坏", style: "样式", other: "其他" };
  var FILTERS = [
    ["all", "全部"],
    ["term", "术语"],
    ["ocr", "OCR"],
    ["high", "高优先"],
    ["pending", "待裁决"],
    ["decided", "已裁决"]
  ];

  function $(selector) { return document.querySelector(selector); }

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  function highlight(text, terms) {
    var source = String(text == null ? "" : text);
    var list = Array.from(new Set((terms || []).map(function (term) {
      return String(term || "");
    }).filter(Boolean))).sort(function (a, b) { return b.length - a.length; });
    if (!list.length) return esc(source);
    var out = "", index = 0;
    while (index < source.length) {
      var best = -1, chosen = "";
      for (var i = 0; i < list.length; i += 1) {
        var found = source.indexOf(list[i], index);
        if (found === -1) continue;
        if (best === -1 || found < best || (found === best && list[i].length > chosen.length)) {
          best = found;
          chosen = list[i];
        }
      }
      if (best === -1) { out += esc(source.slice(index)); break; }
      out += esc(source.slice(index, best)) + "<mark>" + esc(chosen) + "</mark>";
      index = best + chosen.length;
    }
    return out;
  }

  function issueById(id) {
    return state.issues.find(function (issue) { return issue.id === id; }) || null;
  }

  function currentIssue() {
    return state.currentId ? issueById(state.currentId) : null;
  }

  function isPending(issue) {
    return issue.status === "pending" || issue.status === "deferred";
  }

  function mainVariant(issue) {
    var variants = issue.variants || [];
    return variants.slice().sort(function (a, b) {
      return (b.count || 0) - (a.count || 0) || (b.notices || 0) - (a.notices || 0);
    })[0] || null;
  }

  function matching(name) {
    if (name === "all") return state.issues.slice();
    if (name === "term") return state.issues.filter(function (issue) { return issue.type === "term_conflict"; });
    if (name === "ocr") return state.issues.filter(function (issue) { return issue.type === "ocr_block"; });
    if (name === "high") return state.issues.filter(function (issue) { return isPending(issue) && issue.severity === "high"; });
    if (name === "decided") return state.issues.filter(function (issue) { return issue.status === "decided"; });
    return state.issues.filter(isPending);
  }

  function filtered() { return matching(state.filter); }

  function firstForView(items) {
    if (state.filter === "all" || state.filter === "pending" || state.filter === "decided") {
      return items[0] || null;
    }
    return items.find(isPending) || null;
  }

  function riskLabel(issue) {
    if (issue.type !== "ocr_block" || !issue.risk) return "";
    return RISK_LABEL[issue.risk] || issue.risk;
  }

  function optionLabel(issue, value) {
    var option = (issue.options || []).find(function (item) { return item.value === value; });
    return option ? option.label : (value || "");
  }

  function fmtTime(value) {
    if (!value) return "";
    var date = new Date(value);
    if (isNaN(date.getTime())) return String(value);
    return date.toLocaleString("zh-CN", {
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit"
    });
  }

  async function api(path, options) {
    var response = await fetch(path, options);
    var type = response.headers.get("content-type") || "";
    var body = type.indexOf("application/json") !== -1
      ? await response.json().catch(function () { return {}; })
      : await response.text();
    if (!response.ok) {
      throw new Error((body && body.error) || body || ("HTTP " + response.status));
    }
    return body;
  }

  function applyQueue(data) {
    state.issues = Array.isArray(data.issues) ? data.issues : [];
    state.decisions = data.decisions && typeof data.decisions === "object" ? data.decisions : {};
    state.stats = data.stats || {};
    state.source = data.source || "";
  }

  function hydrate(issue, pushHistory) {
    if (pushHistory && state.currentId && state.currentId !== issue.id) {
      state.history.push(state.currentId);
    }
    state.currentId = issue.id;
    state.selected = null;
    state.preferred = null;
    state.overrideText = issue.corrected || "";
    state.note = "";
    state.contextExpanded = {};
    var decision = issue.decision;
    if (decision && decision.decision) {
      var known = (issue.options || []).some(function (option) { return option.value === decision.decision; });
      if (known) state.selected = decision.decision;
      if (decision.note) state.note = decision.note;
      if (decision.preferred) state.preferred = decision.preferred;
      if (decision.corrected) state.overrideText = decision.corrected;
    }
    if (state.selected === "same_alias" && !state.preferred) {
      var main = mainVariant(issue);
      if (main) state.preferred = main.text;
    }
    renderAll();
  }

  function openIssue(id, pushHistory) {
    var issue = issueById(id);
    if (!issue) return;
    hydrate(issue, pushHistory !== false);
  }

  function renderStats() {
    var stats = state.stats || {};
    var total = stats.total || 0;
    var decided = stats.decided || 0;
    $("#statPending").textContent = stats.pending || 0;
    $("#statHigh").textContent = stats.high || 0;
    $("#statDecided").textContent = decided;
    var percent = total ? Math.round(decided * 100 / total) : 0;
    $("#barFill").style.width = percent + "%";
    $("#progressText").textContent = decided + " / " + total + " 已裁决";
  }

  function renderFilters() {
    $("#filters").innerHTML = FILTERS.map(function (pair) {
      var key = pair[0], label = pair[1];
      var count = matching(key).length;
      return '<button type="button" class="chip ' + (state.filter === key ? "on" : "") +
        '" data-action="filter" data-filter="' + key + '">' + label +
        '<span class="chip-count">' + count + "</span></button>";
    }).join("");
  }

  function renderQueue() {
    var items = filtered();
    $("#queueList").innerHTML = items.length
      ? items.map(function (issue) {
          var dot = issue.status === "decided" ? "decided" : (issue.status === "deferred" ? "deferred" : (issue.severity === "high" ? "high" : ""));
          var type = TYPE_LABEL[issue.type] || issue.type || "";
          var risk = riskLabel(issue);
          var status = issue.status === "decided"
            ? "已裁决"
            : (issue.status === "deferred" ? "稍后" : (SEV_LABEL[issue.severity] || issue.severity || "待裁决"));
          return '<button type="button" class="qitem ' + (state.currentId === issue.id ? "current" : "") +
            '" data-action="select-issue" data-id="' + esc(issue.id) + '">' +
            '<span class="qtitle">' + esc(issue.title) + "</span>" +
            '<span class="qmeta"><span class="dot ' + dot + '"></span>' +
            esc(type) + (risk ? " · " + esc(risk) : "") + " · " + esc(status) + "</span></button>";
        }).join("")
      : '<div class="qempty">没有符合条件的题目</div>';
    var source = state.source === "fixture" ? "示例数据（tools.review 未就绪）" : "数据层 · tools.review";
    $("#queueFoot").textContent = source + " · " + state.issues.length + " 题";
  }

  function severityTag(issue) {
    var label = SEV_LABEL[issue.severity] || issue.severity || "";
    return '<span class="tag ' + (issue.severity === "high" ? "high" : "") + '">' + esc(label) + "</span>";
  }

  function riskTag(issue) {
    var label = riskLabel(issue);
    if (!label) return "";
    return '<span class="tag risk ' + (issue.risk === "source_damage" ? "damage" : "") + '">' +
      esc(label) + "</span>";
  }

  function decidedBanner(issue) {
    if (issue.status !== "decided" || !issue.decision) return "";
    var decision = issue.decision;
    var text = "已裁决：" + optionLabel(issue, decision.decision);
    if (decision.decided_at) text += " · " + fmtTime(decision.decided_at);
    if (decision.note) text += " · 备注：" + decision.note;
    return '<div class="banner"><span>' + esc(text) +
      '</span><button type="button" class="btn" data-action="reopen">改回待裁决</button></div>';
  }

  function whyBlock(issue) {
    var why = issue.why || issue.question || "这一处文本需要人工确认。";
    return '<div class="why"><b>为什么需要你</b>' + esc(why) + "</div>";
  }

  function recoBlock(issue) {
    if (!issue.recommendation) return "";
    return '<div class="reco"><b>建议 · 仅供参考</b>' + esc(issue.recommendation) + "</div>";
  }

  function contextCard(context, terms) {
    var source = [];
    if (context.notice_date) source.push(esc(context.notice_date));
    if (context.title) source.push(esc(context.title));
    if (context.notice_id) {
      source.push('<a href="/api/notice?id=' + encodeURIComponent(context.notice_id) +
        '" target="_blank" rel="noopener">原文</a>');
    }
    return '<div class="ctx">' +
      (source.length ? '<div class="src">' + source.join("<span>·</span>") + "</div>" : "") +
      (context.before ? '<div class="nb">' + esc(context.before) + "</div>" : "") +
      '<div class="line">' + highlight(context.line, terms) + "</div>" +
      (context.after ? '<div class="nb">' + esc(context.after) + "</div>" : "") +
      "</div>";
  }

  function renderTerm(issue) {
    var variants = Array.isArray(issue.variants) ? issue.variants : [];
    var contexts = Array.isArray(issue.contexts) ? issue.contexts : [];
    var terms = variants.map(function (variant) { return variant.text; });
    var main = mainVariant(issue);
    var impact = issue.impact || {};
    var gain = impact.search_gain != null ? impact.search_gain : (main ? main.count : 0);
    var notices = impact.total_notices != null ? impact.total_notices : (main ? main.notices : 0);

    var variantCards = variants.map(function (variant) {
      var isMain = main && variant.text === main.text;
      return '<div class="variant ' + (isMain ? "main" : "") + '">' +
        (isMain ? '<span class="vflag">主写法</span>' : "") +
        '<span class="vtext">' + esc(variant.text) + "</span>" +
        '<span class="vcounts">' + (variant.count || 0) + " 处 · " + (variant.notices || 0) + " 篇公告</span>" +
        "</div>";
    }).join("");

    var groups = variants.map(function (variant) {
      return {
        text: variant.text,
        items: contexts.filter(function (context) { return context.variant === variant.text; })
      };
    });
    var orphans = contexts.filter(function (context) {
      return !variants.some(function (variant) { return variant.text === context.variant; });
    });
    if (orphans.length) groups.push({ text: "其他", items: orphans });

    var contextColumns = groups.map(function (group) {
      var key = group.text;
      var expanded = !!state.contextExpanded[key];
      var shown = expanded ? group.items : group.items.slice(0, 3);
      var cards = shown.map(function (context) { return contextCard(context, terms); }).join("");
      var more = group.items.length > 3
        ? '<button type="button" class="more" data-action="toggle-context" data-key="' + esc(key) + '">' +
          (expanded ? "收起" : "展开全部 " + group.items.length + " 条") + "</button>"
        : "";
      return '<div class="ctx-col"><h4>「' + esc(group.text) + "」上下文</h4>" +
        (cards || '<div class="ctx-empty">暂无上下文</div>') + more + "</div>";
    }).join("");

    var impactLine = "合并后，主写法「" + esc(main ? main.text : "") + "」的检索覆盖增加 <b>" +
      esc(gain) + "</b> 处；共涉及 <b>" + esc(notices) + "</b> 篇公告。";
    var matchNote = issue.match_note
      ? '<p class="match-note">统计口径：' + esc(issue.match_note) + "</p>"
      : "";

    return '<article class="issue">' +
      decidedBanner(issue) +
      '<div class="kicker"><span class="tag">术语冲突</span>' + severityTag(issue) + "</div>" +
      "<h2>" + esc(issue.title) + "</h2>" +
      whyBlock(issue) +
      (issue.question ? '<p class="question">' + esc(issue.question) + "</p>" : "") +
      recoBlock(issue) +
      '<h3 class="sec">两种写法</h3>' +
      '<div class="variants">' + variantCards + "</div>" +
      '<div class="impact">' + impactLine + "</div>" +
      matchNote +
      '<h3 class="sec">上下文</h3>' +
      '<div class="ctx-cols">' + contextColumns + "</div>" +
      decisionBlock(issue) +
      "</article>";
  }

  function cropUrl(issue) {
    var spec = issue.crop || {};
    var notice = spec.notice || issue.notice || "";
    var image = spec.image != null ? spec.image : issue.image_index;
    var block = spec.block != null ? spec.block : issue.block_index;
    if (!notice || image == null || block == null) return "";
    var params = new URLSearchParams({
      notice: notice,
      image: String(image),
      block: String(block),
      scale: String(spec.scale || 2)
    });
    return "/api/crop?" + params.toString();
  }

  function renderOcr(issue) {
    var source = cropUrl(issue);
    var crop = source
      ? '<figure class="crop"><img src="' + esc(source) + '" alt="源图 crop" data-action="zoom" data-src="' +
        esc(source) + '"><figcaption>点击放大 · 图 ' + esc(issue.image_index != null ? issue.image_index : "") +
        " · block " + esc(issue.block_index != null ? issue.block_index : "") + "</figcaption></figure>"
      : '<div class="crop-missing">缺少 crop 位置（notice/image/block）</div>';
    var confidence = issue.confidence
      ? '<span class="conf">confidence: ' + esc(issue.confidence) + "</span>"
      : "";
    var noticeLink = issue.notice
      ? ' · <a href="/api/notice?id=' + encodeURIComponent(issue.notice) +
        '" target="_blank" rel="noopener">整篇公告</a>'
      : "";
    var before = Array.isArray(issue.context_before) ? issue.context_before : [];
    var after = Array.isArray(issue.context_after) ? issue.context_after : [];
    var contextRows = before.map(function (line) {
      return '<div class="row"><span>前文</span><div>' + esc(line) + "</div></div>";
    }).join("") + after.map(function (line) {
      return '<div class="row"><span>后文</span><div>' + esc(line) + "</div></div>";
    }).join("");

    return '<article class="issue">' +
      decidedBanner(issue) +
      '<div class="kicker"><span class="tag">OCR 复核</span>' + severityTag(issue) + riskTag(issue) +
      confidence + "</div>" +
      "<h2>" + esc(issue.title) + "</h2>" +
      whyBlock(issue) +
      (issue.question ? '<p class="question">' + esc(issue.question) + "</p>" : "") +
      recoBlock(issue) +
      '<div class="ocr-grid">' + crop +
        '<div class="ocr-side">' +
          '<div class="field"><label>OCR 原文</label><p class="mono">' + esc(issue.original) + "</p></div>" +
          '<div class="field"><label>当前修正</label><p class="mono">' + esc(issue.corrected) + "</p></div>" +
          '<div class="field"><label>修正理由</label><p>' + esc(issue.reason) + "</p></div>" +
          '<div class="field"><label>公告</label><p>' + esc(issue.notice_date || "") + " · " +
            esc(issue.notice_title || issue.notice || "") + noticeLink + "</p></div>" +
        "</div>" +
      "</div>" +
      (contextRows ? '<div class="ctx-strip">' + contextRows + "</div>" : "") +
      decisionBlock(issue) +
      "</article>";
  }

  function renderGeneric(issue) {
    return '<article class="issue">' +
      decidedBanner(issue) +
      '<div class="kicker"><span class="tag">' + esc(TYPE_LABEL[issue.type] || issue.type) + "</span>" +
      severityTag(issue) + riskTag(issue) + "</div>" +
      "<h2>" + esc(issue.title) + "</h2>" +
      whyBlock(issue) +
      (issue.question ? '<p class="question">' + esc(issue.question) + "</p>" : "") +
      recoBlock(issue) +
      '<h3 class="sec">详情</h3><pre class="details-json">' +
      esc(JSON.stringify(issue, null, 2)) + "</pre>" +
      decisionBlock(issue) +
      "</article>";
  }

  function decisionBlock(issue) {
    var options = issue.options || [];
    var buttons = options.map(function (option, index) {
      return '<button type="button" class="option ' + (state.selected === option.value ? "sel" : "") +
        '" data-action="set-option" data-value="' + esc(option.value) + '" aria-pressed="' +
        (state.selected === option.value ? "true" : "false") + '">' +
        '<span class="key">' + (index + 1) + "</span><span>" +
        '<span class="opt-label">' + esc(option.label || option.value) + "</span>" +
        (option.description ? '<span class="opt-desc">' + esc(option.description) + "</span>" : "") +
        "</span></button>";
    }).join("");

    var preferred = "";
    if (state.selected === "same_alias" && (issue.variants || []).length) {
      preferred = '<div class="preferred"><p>保留哪个写法为主？合并检索时以主写法覆盖两种写法。</p>' +
        '<div class="pref-row">' + issue.variants.map(function (variant) {
          return '<button type="button" class="pref ' + (state.preferred === variant.text ? "on" : "") +
            '" data-action="set-preferred" data-value="' + esc(variant.text) + '">' +
            esc(variant.text) + "<small>" + esc(variant.count || 0) + " 处</small></button>";
        }).join("") + "</div></div>";
    }

    var override = state.selected === "override"
      ? '<div class="override"><label for="overrideText">改为你确认的文字</label>' +
        '<textarea id="overrideText" data-field="override" placeholder="输入正确的文字">' +
        esc(state.overrideText) + "</textarea></div>"
      : "";

    return '<section class="decision">' +
      '<h3 class="sec">你的决定</h3>' +
      '<div class="opts" role="group" aria-label="处理方式">' + buttons + "</div>" +
      preferred + override +
      '<div class="note"><label for="note">备注（可选，会写入决定账本）</label>' +
      '<textarea id="note" data-field="note" placeholder="例如：官方两种写法都出现，检索时合并">' +
      esc(state.note) + "</textarea></div>" +
      '<div class="savebar">' +
        '<button type="button" class="btn ghost" data-action="prev" ' + (state.history.length ? "" : "disabled") +
          '>← 上一题</button>' +
        '<button type="button" class="btn" data-action="defer" ' + (state.busy ? "disabled" : "") +
          '>稍后 <kbd>S</kbd></button>' +
        '<span class="spacer"></span>' +
        '<span class="hint">键盘 1-' + Math.min(options.length, 9) + ' 选择 · Enter 保存</span>' +
        '<button type="button" class="btn primary" data-action="save-next" ' + (state.busy ? "disabled" : "") +
          '>保存并下一题 <kbd>⏎</kbd></button>' +
      "</div></section>";
  }

  function emptyState() {
    var total = state.issues.length;
    var pending = state.issues.filter(isPending).length;
    var title, body, actions;
    if (total === 0) {
      title = "队列是空的";
      body = "没有待裁决的文本问题。数据层接入后，新的术语冲突和 OCR 复核会出现在这里。";
      actions = '<button type="button" class="btn" data-action="refresh">刷新</button>';
    } else if (pending === 0) {
      title = "所有题目都已裁决";
      body = "待裁决队列已经清空。可以查看已裁决记录，或导出决定交给数据层。";
      actions = '<button type="button" class="btn" data-action="filter" data-filter="decided">查看已裁决</button>' +
        '<button type="button" class="btn" data-action="export">导出决定</button>' +
        '<button type="button" class="btn" data-action="refresh">刷新</button>';
    } else if (state.filter === "term" && !matching("term").some(isPending)) {
      var ocrPending = matching("ocr").filter(isPending).length;
      title = matching("term").length ? "术语冲突都处理完了" : "没有待裁决的术语冲突";
      body = ocrPending
        ? "可以切到 OCR 复核继续；上方仍显示整个队列的进度。"
        : "换一个筛选条件，或查看已裁决记录。";
      actions = (ocrPending
        ? '<button type="button" class="btn" data-action="filter" data-filter="ocr">查看 OCR（' + ocrPending + '）</button>'
        : "") +
        '<button type="button" class="btn" data-action="filter" data-filter="decided">查看已裁决</button>' +
        '<button type="button" class="btn" data-action="filter" data-filter="all">查看全部</button>';
    } else if (state.filter === "ocr" && !matching("ocr").some(isPending)) {
      var termPending = matching("term").filter(isPending).length;
      title = matching("ocr").length ? "OCR 复核都处理完了" : "没有待裁决的 OCR 复核";
      body = termPending
        ? "可以回到术语冲突继续；上方仍显示整个队列的进度。"
        : "换一个筛选条件，或查看已裁决记录。";
      actions = (termPending
        ? '<button type="button" class="btn" data-action="filter" data-filter="term">查看术语（' + termPending + '）</button>'
        : "") +
        '<button type="button" class="btn" data-action="filter" data-filter="decided">查看已裁决</button>' +
        '<button type="button" class="btn" data-action="filter" data-filter="all">查看全部</button>';
    } else {
      title = "这个筛选下没有题目";
      body = "换一个筛选条件，或回到待裁决队列继续。";
      actions = '<button type="button" class="btn" data-action="filter" data-filter="pending">查看待裁决</button>' +
        '<button type="button" class="btn" data-action="filter" data-filter="all">查看全部</button>';
    }
    return '<div class="empty"><h2>' + title + "</h2><p>" + body +
      '</p><div class="actions">' + actions + "</div></div>";
  }

  function renderMain() {
    var issue = currentIssue();
    var main = $("#main");
    if (!issue) {
      main.innerHTML = emptyState();
      return;
    }
    if (issue.type === "term_conflict") main.innerHTML = renderTerm(issue);
    else if (issue.type === "ocr_block") main.innerHTML = renderOcr(issue);
    else main.innerHTML = renderGeneric(issue);
  }

  function renderAll() {
    renderStats();
    renderFilters();
    renderQueue();
    renderMain();
  }

  async function loadQueue(showToast) {
    try {
      applyQueue(await api("/api/queue"));
      if (state.currentId) {
        var current = issueById(state.currentId);
        if (!current) {
          var first = firstForView(filtered());
          if (first) { hydrate(first, false); return; }
          state.currentId = null;
        }
      } else {
        var firstIssue = firstForView(filtered());
        if (firstIssue) { hydrate(firstIssue, false); return; }
      }
      renderAll();
      if (showToast) toast("队列已刷新");
    } catch (error) {
      $("#main").innerHTML = '<div class="empty"><h2>无法连接本地服务</h2><p>' +
        esc(error.message) + '</p><div class="actions"><button type="button" class="btn" data-action="refresh">重试</button></div></div>';
    }
  }

  function selectOption(value) {
    var issue = currentIssue();
    if (!issue) return;
    state.selected = value;
    if (value === "same_alias" && !state.preferred) {
      var main = mainVariant(issue);
      if (main) state.preferred = main.text;
    }
    if (value === "override" && !state.overrideText) {
      state.overrideText = issue.corrected || "";
    }
    renderMain();
  }

  function validateChoice(issue) {
    if (!state.selected) return "请先选择一个处理方式";
    if (state.selected === "same_alias" && (issue.variants || []).length && !state.preferred) {
      return "请选择保留哪个写法";
    }
    if (state.selected === "override" && !String(state.overrideText || "").trim()) {
      return "请输入修正后的文字";
    }
    return "";
  }

  function decisionPayload(issue) {
    if (state.selected === "same_alias") {
      var aliases = (issue.variants || []).map(function (variant) { return variant.text; })
        .filter(function (text) { return text !== state.preferred; });
      return { preferred: state.preferred, aliases: aliases };
    }
    if (state.selected === "override") {
      return { corrected: String(state.overrideText || "").trim() };
    }
    return {};
  }

  function nextPending(excludeId) {
    var name = state.filter === "decided" ? "pending" : state.filter;
    return matching(name).find(function (issue) {
      return isPending(issue) && issue.id !== excludeId;
    }) || null;
  }

  async function postDecision(decision, payload, note, advance) {
    var issue = currentIssue();
    if (!issue || state.busy) return;
    state.busy = true;
    renderMain();
    try {
      var data = await api("/api/decisions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          issue_id: issue.id,
          decision: decision,
          payload: payload || {},
          note: note || ""
        })
      });
      if (data.queue) applyQueue(data.queue);
      else await loadQueue(false);
      state.busy = false;
      toast(decision === "defer" ? "已标记稍后" : "已保存");
      if (advance) {
        var next = nextPending(issue.id);
        if (next) {
          hydrate(next, true);
        } else if (decision === "defer") {
          hydrate(issue, false);
        } else {
          state.currentId = null;
          renderAll();
        }
      } else {
        renderAll();
      }
    } catch (error) {
      state.busy = false;
      renderMain();
      toast("保存失败：" + error.message, 6500);
    }
  }

  function saveNext() {
    var issue = currentIssue();
    if (!issue) return;
    var problem = validateChoice(issue);
    if (problem) { toast(problem); return; }
    postDecision(state.selected, decisionPayload(issue), state.note, true);
  }

  function saveDefer() {
    if (!currentIssue()) return;
    postDecision("defer", {}, state.note, true);
  }

  function goPrev() {
    while (state.history.length) {
      var id = state.history.pop();
      if (issueById(id)) { openIssue(id, false); return; }
    }
    toast("没有上一题");
  }

  async function reopenCurrent() {
    var issue = currentIssue();
    if (!issue) return;
    try {
      var data = await api("/api/decisions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          issue_id: issue.id,
          decision: "defer",
          payload: {},
          note: "重新打开，待重新裁决"
        })
      });
      if (data.queue) applyQueue(data.queue);
      else await loadQueue(false);
      openIssue(issue.id, false);
      toast("已改回待裁决");
    } catch (error) {
      toast("操作失败：" + error.message, 6500);
    }
  }

  async function exportDecisions() {
    try {
      var data = await api("/api/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}"
      });
      toast("已导出：" + summarizeExport(data.result), 6000);
    } catch (error) {
      toast("导出失败：" + error.message, 6500);
    }
  }

  function summarizeExport(result) {
    if (result == null) return "完成";
    if (Array.isArray(result)) return result.join("、");
    if (typeof result === "object") {
      var paths = result.paths || result.files;
      if (!Array.isArray(paths)) {
        paths = [result.terms_path, result.ocr_decisions_path].filter(Boolean);
      }
      if (paths.length) {
        var counts = [];
        if (result.terms != null) counts.push("术语 " + result.terms);
        if (result.ocr != null) counts.push("OCR " + result.ocr);
        if (result.stale) counts.push("stale " + result.stale);
        return paths.join("、") + (counts.length ? "（" + counts.join(" · ") + "）" : "");
      }
      var parts = Object.keys(result).slice(0, 5).map(function (key) {
        var value = result[key];
        return key + "=" + (typeof value === "object" ? JSON.stringify(value) : String(value));
      });
      return parts.join(" · ").slice(0, 400) || "完成";
    }
    return String(result);
  }

  function switchFilter(name) {
    state.filter = name;
    state.history = [];
    var first = firstForView(filtered());
    if (first) { hydrate(first, false); return; }
    state.currentId = null;
    renderAll();
  }

  function toggleQueue() {
    var pane = $("#queuePane");
    var open = pane.classList.toggle("open");
    var button = document.querySelector(".queue-toggle");
    if (button) button.setAttribute("aria-expanded", open ? "true" : "false");
  }

  function closeQueue() {
    var pane = $("#queuePane");
    if (pane) pane.classList.remove("open");
    var button = document.querySelector(".queue-toggle");
    if (button) button.setAttribute("aria-expanded", "false");
  }

  function openLightbox(src) {
    if (!src) return;
    $("#lightboxImg").src = src;
    $("#lightbox").classList.add("open");
  }

  function closeLightbox() {
    $("#lightbox").classList.remove("open");
    $("#lightboxImg").removeAttribute("src");
  }

  var toastTimer = null;
  function toast(message, duration) {
    var element = $("#toast");
    element.textContent = message;
    element.classList.add("show");
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { element.classList.remove("show"); }, duration || 2800);
  }

  document.addEventListener("click", function (event) {
    var target = event.target.closest("[data-action]");
    if (!target) return;
    var action = target.dataset.action;
    if (action === "select-issue") {
      openIssue(target.dataset.id);
      closeQueue();
    } else if (action === "filter") {
      switchFilter(target.dataset.filter);
      closeQueue();
    } else if (action === "set-option") {
      selectOption(target.dataset.value);
    } else if (action === "set-preferred") {
      state.preferred = target.dataset.value;
      renderMain();
    } else if (action === "toggle-context") {
      var key = target.dataset.key;
      state.contextExpanded[key] = !state.contextExpanded[key];
      renderMain();
    } else if (action === "zoom") {
      openLightbox(target.dataset.src);
    } else if (action === "close-lightbox") {
      closeLightbox();
    } else if (action === "save-next") {
      saveNext();
    } else if (action === "defer") {
      saveDefer();
    } else if (action === "prev") {
      goPrev();
    } else if (action === "reopen") {
      reopenCurrent();
    } else if (action === "export") {
      exportDecisions();
    } else if (action === "refresh") {
      loadQueue(true);
    } else if (action === "toggle-queue") {
      toggleQueue();
    }
  });

  document.addEventListener("input", function (event) {
    var field = event.target.dataset ? event.target.dataset.field : "";
    if (field === "note") state.note = event.target.value;
    if (field === "override") state.overrideText = event.target.value;
  });

  document.addEventListener("keydown", function (event) {
    if (event.altKey || event.ctrlKey || event.metaKey) {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        saveNext();
      }
      return;
    }
    var tag = (event.target.tagName || "").toLowerCase();
    var typing = tag === "textarea" || tag === "input" || tag === "select" || event.target.isContentEditable;
    if (event.key === "Escape") {
      closeLightbox();
      closeQueue();
      return;
    }
    if (typing) return;
    if (/^[1-9]$/.test(event.key)) {
      var issue = currentIssue();
      if (!issue) return;
      var option = (issue.options || [])[Number(event.key) - 1];
      if (option) {
        event.preventDefault();
        selectOption(option.value);
      }
    } else if (event.key === "Enter") {
      event.preventDefault();
      saveNext();
    } else if (event.key === "s" || event.key === "S") {
      event.preventDefault();
      saveDefer();
    } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      event.preventDefault();
      goPrev();
    }
  });

  loadQueue(false);
})();
</script>
</body>
</html>
"""


__all__ = [
    "BadRequest",
    "CropSpec",
    "DEFAULT_PORT",
    "FIXTURE_PATH",
    "PAGE_HTML",
    "PAGE_TITLE",
    "ReviewStore",
    "ReviewUnavailable",
    "compute_stats",
    "crop_png",
    "decorate_issues",
    "filter_issues",
    "import_backend",
    "load_fixture",
    "main",
    "make_server",
    "normalize_decisions",
    "notice_path",
    "parse_crop_params",
    "read_notice",
    "serve",
]


if __name__ == "__main__":
    raise SystemExit(main())
