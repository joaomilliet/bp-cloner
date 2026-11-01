"""BP Cloner: drives the BP Cloner capture app on this Mac through its local control API.

The app listens on 127.0.0.1:47811 (loopback only) and answers POST /rpc {"method", "params"} with JSON - the
same methods its own window uses. Plugins are named as AiAudioHub's plug-in library lists them; only the plugins
added to the chain are loaded.
"""
from __future__ import annotations

import http.client
import json
import socket

from .toolkit import ToolError, prop, tool

MODULE = "bpcloner"
HOST, PORT = "127.0.0.1", 47811

ALGORITHMS = ["auto", "linear", "parametric_eq", "hammerstein", "neural", "dynamics"]
QUALITIES = ["rapido", "padrao", "maximo"]
RATES = [44100, 48000, 88200, 96000]

_STAGE = prop("string", "The stage id, as status or add_stage answers it (s1, s2...).", minLength=1, maxLength=20)


def _rpc(method: str, params: dict | None = None, timeout: float = 30.0):
    body = json.dumps({"method": method, "params": params or {}})
    conn = http.client.HTTPConnection(HOST, PORT, timeout=timeout)
    try:
        conn.request("POST", "/rpc", body=body.encode("utf-8"), headers={"Content-Type": "application/json"})
        res = conn.getresponse()
        raw = res.read()
    except ConnectionRefusedError:
        raise ToolError("BP Cloner is not open on this Mac: open the BP Cloner app, then call again", "app_closed")
    except socket.timeout:
        raise ToolError("BP Cloner did not answer in time (it may be loading a plugin); call status in a moment",
                        "timeout")
    except OSError as e:
        raise ToolError("could not reach BP Cloner on 127.0.0.1:%d (%s)" % (PORT, e), "unreachable")
    finally:
        conn.close()
    try:
        answer = json.loads(raw.decode("utf-8") or "null")
    except ValueError:
        raise ToolError("BP Cloner answered something that is not JSON", "bad_answer")
    if isinstance(answer, dict) and answer.get("error"):
        raise ToolError("BP Cloner: %s" % answer["error"], "refused")
    return answer


def _pick(text, lang: str = "en") -> str:
    """A text the engine stored in both languages ({en, pt}) or a plain string."""
    if isinstance(text, dict):
        return text.get(lang) or text.get("en") or ""
    return text or ""


def _stage_ids() -> list:
    return [s.get("id") for s in (_rpc("status").get("chain") or [])]


def _need_stage(stage: str) -> None:
    ids = _stage_ids()
    if stage not in ids:
        raise ToolError("no stage %r in the chain" % stage, "not_found", "stage", ids)


# ---- reading ------------------------------------------------------------------------------------------------------
@tool("status", "Whether BP Cloner is open, the chain (each stage: id, plugin, category, algorithm, loaded or not), "
      "the capture settings, and the running capture's progress.", module=MODULE, timeout_ms=15000)
def status(a: dict) -> dict:
    s = _rpc("status")
    return {"app": s.get("app"), "version": s.get("version"), "engineReady": s.get("engineOk"),
            "chain": [{k: st.get(k) for k in ("id", "name", "manufacturer", "format", "category", "algorithm", "ready",
                                              "loading", "error", "latency")} for st in s.get("chain") or []],
            "chainReady": s.get("chainReady"), "settings": s.get("settings"), "capturing": s.get("capturing"),
            "capture": s.get("capture"), "lastReport": s.get("lastReport")}


@tool("find_plugins", "Plugins installed on this Mac whose name or maker matches, as BP Cloner can load them (names "
      "only - nothing is loaded). Use the names library__list_plugins gives.",
      props={"query": prop("string", "Part of the name or maker, e.g. '1176' or 'Pultec'.", maxLength=80),
             "category": prop("string", "Only this category (EQ, Dynamics, Saturation, Reverb, Delay, Modulation, "
                                          "Imaging, Utility...).", maxLength=40),
             "limit": prop("integer", "At most this many (default 30).", minimum=1, maximum=200)},
      module=MODULE, timeout_ms=20000)
def find_plugins(a: dict) -> dict:
    found = _rpc("findPlugins", {"query": a.get("query", ""), "category": a.get("category", ""),
                                 "limit": int(a.get("limit", 30))})
    return {"plugins": [{k: p.get(k) for k in ("name", "format", "category", "manufacturer", "shell")} for p in found]}


@tool("list_parameters", "A stage's plugin parameters: index, name, current value (0..1) and how the plugin shows it.",
      props={"stage": _STAGE,
             "search": prop("string", "Only parameters whose name contains this, e.g. 'threshold'.", maxLength=80)},
      required=("stage",), module=MODULE, timeout_ms=20000)
def list_parameters(a: dict) -> dict:
    _need_stage(a["stage"])
    return _rpc("getParameters", {"id": a["stage"], "query": a.get("search", "")})


@tool("capture_report", "The result of a finished capture: each stage's model, what it does, and its fidelity "
      "(null depth on music not used to fit it), plus the whole chain's. Default: the last capture.",
      props={"report": prop("string", "A report path from list_captures (default: the last capture).", maxLength=1000),
             "language": prop("string", "en or pt for the descriptions.", enum=["en", "pt"])},
      module=MODULE, timeout_ms=20000)
def capture_report(a: dict) -> dict:
    r = _rpc("getReport", {"path": a["report"]} if a.get("report") else {})
    if not r:
        raise ToolError("no finished capture yet: start_capture first, or pick one from list_captures", "no_report")
    lang = a.get("language", "en")
    return {"name": r.get("name"), "bpc": r.get("bpc"), "sampleRate": r.get("sampleRate"),
            "latencySamples": r.get("latencySamples"), "chainNullDb": (r.get("validation") or {}).get("nullDb"),
            "stages": [{"name": s.get("name"), "model": s.get("model"),
                        "behaviours": (s.get("analysis") or {}).get("behaviours"),
                        "summary": _pick((s.get("analysis") or {}).get("summary"), lang),
                        "nullDb": (s.get("fidelity") or {}).get("nullDb"),
                        "spectralErrorDb": (s.get("fidelity") or {}).get("spectralErrorDb"),
                        "notes": [_pick(n, lang) for n in (s.get("analysis") or {}).get("notes") or []]}
                       for s in r.get("stages") or []]}


@tool("list_captures", "Finished captures, newest first: name, date, stage count, chain fidelity, report path.",
      module=MODULE, timeout_ms=20000)
def list_captures(a: dict) -> dict:
    return {"captures": _rpc("listSessions")}


# ---- changing -----------------------------------------------------------------------------------------------------
@tool("add_stage", "Adds a plugin at the end of the chain. Give its name as library__list_plugins lists it; pass the "
      "library's category too. Only this plugin is loaded (a Waves shell is opened the first time). Answers the stage id.",
      props={"plugin": prop("string", "The plugin's name, e.g. 'UAD Pultec EQP-1A'.", minLength=1, maxLength=120),
             "format": prop("string", "VST3 (default) or AU.", enum=["VST3", "AU"]),
             "category": prop("string", "The library's category (EQ, Dynamics, Saturation...). Helps Auto pick probes.",
                              maxLength=40),
             "algorithm": prop("string", "auto (default: measurements decide), linear, parametric_eq, hammerstein "
                                          "(saturation), neural (NAM) or dynamics (compressor/limiter).",
                               enum=ALGORITHMS)},
      required=("plugin",), writes=True, module=MODULE, timeout_ms=300000)
def add_stage(a: dict) -> dict:
    params = {"name": a["plugin"], "format": a.get("format", "VST3"), "algorithm": a.get("algorithm", "auto")}
    if a.get("category"):
        params["category"] = a["category"]
    return _rpc("addStage", params, timeout=300.0)


@tool("set_parameter", "Sets one parameter of a stage's plugin: by its name or index (list_parameters), to a value "
      "as the plugin shows it ('text': '-3', '4:1') or to a normalised 'value' 0..1. Answers what the plugin now shows.",
      props={"stage": _STAGE,
             "parameter": prop("string", "The parameter's name or index.", minLength=1, maxLength=120),
             "text": prop("string", "The value as the plugin displays it.", maxLength=40),
             "value": prop("number", "Normalised value, 0..1.", minimum=0, maximum=1)},
      required=("stage", "parameter"), writes=True, module=MODULE, timeout_ms=20000)
def set_parameter(a: dict) -> dict:
    if "text" not in a and "value" not in a:
        raise ToolError("give `text` (as the plugin shows it) or `value` (0..1)", "missing", "text")
    _need_stage(a["stage"])
    p = {"id": a["stage"], "parameter": a["parameter"]}
    if "text" in a:
        p["text"] = a["text"]
    else:
        p["value"] = float(a["value"])
    return _rpc("setParameter", p)


@tool("set_algorithm", "How one stage is modelled: auto (measurements decide), linear, parametric_eq, hammerstein, "
      "neural or dynamics.",
      props={"stage": _STAGE, "algorithm": prop("string", "The algorithm.", enum=ALGORITHMS)},
      required=("stage", "algorithm"), writes=True, module=MODULE, timeout_ms=15000)
def set_algorithm(a: dict) -> dict:
    _need_stage(a["stage"])
    _rpc("setAlgorithm", {"id": a["stage"], "algorithm": a["algorithm"]})
    return {"stage": a["stage"], "algorithm": a["algorithm"]}


@tool("remove_stage", "Removes one stage from the chain.", props={"stage": _STAGE}, required=("stage",),
      writes=True, module=MODULE, timeout_ms=15000)
def remove_stage(a: dict) -> dict:
    _need_stage(a["stage"])
    _rpc("removeStage", {"id": a["stage"]})
    return {"removed": a["stage"], "chain": _stage_ids()}


@tool("move_stage", "Moves a stage to a position in the signal order (1 = first).",
      props={"stage": _STAGE, "position": prop("integer", "New position, 1 = first.", minimum=1, maximum=64)},
      required=("stage", "position"), writes=True, module=MODULE, timeout_ms=15000)
def move_stage(a: dict) -> dict:
    _need_stage(a["stage"])
    _rpc("moveStage", {"id": a["stage"], "index": int(a["position"]) - 1})
    return {"chain": _stage_ids()}


@tool("clear_chain", "Removes every stage from the chain.", writes=True, module=MODULE, timeout_ms=15000)
def clear_chain(a: dict) -> dict:
    _rpc("clearChain")
    return {"chain": []}


_SETTINGS = {"name": prop("string", "The capture's name.", minLength=1, maxLength=120),
             "quality": prop("string", "rapido (analytic models, ~2 min per stage), padrao (adds a neural network "
                                       "when saturation needs it) or maximo.", enum=QUALITIES),
             "sample_rate": prop("integer", "The model's sample rate.", enum=RATES),
             "reference_level": prop("number", "Average level into the chain, dBFS RMS (default -18).",
                                     minimum=-30, maximum=-6),
             "language": prop("string", "The app's language.", enum=["en", "pt"])}


def _settings(a: dict) -> dict:
    out = {}
    for key, api in (("name", "name"), ("quality", "quality"), ("sample_rate", "sampleRate"),
                     ("reference_level", "referenceLevel"), ("language", "language")):
        if key in a:
            out[api] = a[key]
    return out


@tool("capture_settings", "Changes the capture settings: name, quality, sample rate, reference level, language.",
      props=_SETTINGS, writes=True, module=MODULE, timeout_ms=15000)
def capture_settings(a: dict) -> dict:
    s = _settings(a)
    if not s:
        raise ToolError("give at least one setting to change", "missing")
    return {"settings": _rpc("setSettings", s)}


@tool("start_capture", "Starts capturing the chain (it plays test signals and music through every plugin, models "
      "each stage, writes a .bpc). Settings given here are applied first. Follow it with capture_progress.",
      props=_SETTINGS, writes=True, module=MODULE, timeout_ms=20000)
def start_capture(a: dict) -> dict:
    _rpc("startCapture", _settings(a))
    return {"started": True, "next": "capture_progress until it is done, then capture_report"}


@tool("capture_progress", "The running capture: phase, current step, per-stage results so far; when it ended, the "
      "outcome and the .bpc path.", module=MODULE, timeout_ms=15000)
def capture_progress(a: dict) -> dict:
    s = _rpc("status")
    c = s.get("capture") or {}
    return {"capturing": s.get("capturing"), **{k: c.get(k) for k in ("phase", "step", "fraction", "stage", "stages",
                                                                      "outcome", "message", "bpc", "report",
                                                                      "warnings")}}


@tool("cancel_capture", "Stops the running capture.", writes=True, module=MODULE, timeout_ms=15000)
def cancel_capture(a: dict) -> dict:
    _rpc("cancelCapture")
    return {"cancelled": True}


@tool("export_capture", "Copies a capture's .bpc (default: the last one) to a folder or file path, for BP Player.",
      props={"path": prop("string", "A folder or a file path ending in .bpc.", minLength=1, maxLength=1000),
             "name": prop("string", "File name when `path` is a folder.", maxLength=120),
             "bpc": prop("string", "Which .bpc (capture_report's `bpc`); default the last capture.", maxLength=1000)},
      required=("path",), writes=True, module=MODULE, timeout_ms=30000)
def export_capture(a: dict) -> dict:
    p = {"path": a["path"]}
    for k in ("name", "bpc"):
        if a.get(k):
            p[k] = a[k]
    return {"exported": _rpc("exportBpc", p)}
