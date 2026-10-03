#!/usr/bin/env python3
"""Point-anchored, evidence-backed dynamic explanations; never speaks or moves."""

import base64
import json
import os
import threading
import time
from pathlib import Path

from guide_board_processing import prepare_tiles, run_board_omni
from guide_knowledge import KnowledgeStore

PROJECT = Path(__file__).resolve().parent.parent
_LOCK = threading.Lock()

OBSERVATION_POLICY = {
    "enabled": False,
    "reason": "G1 摄像头、目标展板关联与观测位姿调整尚未验收，不会自动转身或靠近",
    "anchor": "registered_point_id_and_map_fingerprint",
    "prior_authority": "current_point_prior",
    "proposed_max_translation_m": 0.30,
    "proposed_max_yaw_rad": 0.35,
    "proposed_max_attempts": 2,
    "requires": ["verified_camera", "same_exhibit_association", "fresh_localization",
                 "collision_checked_navigation", "operator_task_authorization"],
    "can_overwrite_prior": False,
}


def validate_presentation(text, context):
    text = text.strip()
    if text.startswith("```json") and text.endswith("```"):
        text = text[7:-3].strip()
    data = json.loads(text)
    if not isinstance(data, dict) or set(data) != {"segments", "source_ids", "needs_review", "uncertainties"}:
        raise ValueError("Omni 未返回完整的 conception 结果")
    segments = data["segments"]
    if not isinstance(segments, list) or len(segments) > 4 or any(
        not isinstance(item, str) or not item.strip() or len(item.strip()) > 150 for item in segments
    ) or sum(len(item.strip()) for item in segments) > 450:
        raise ValueError("生成讲解过长或格式错误")
    if type(data["needs_review"]) is not bool or (not segments and not data["needs_review"]):
        raise ValueError("讲解审核状态无效")
    warnings = data["uncertainties"]
    if not isinstance(warnings, list) or len(warnings) > 8 or any(
        not isinstance(item, str) or len(item) > 200 for item in warnings
    ):
        raise ValueError("讲解核对提示格式无效")
    sources = data["source_ids"]
    if not isinstance(sources, list) or len(sources) > 16 or any(
        not isinstance(item, str) or not item or len(item) > 128 for item in sources
    ):
        raise ValueError("讲解引用格式无效")
    # Only references to authoritative prior / actually supplied photos may
    # substantiate a presentation. Candidate OCR or pending PDFs do not.
    allowed = {item["source_id"] for item in context.get("reviewed_prior", [])}
    allowed |= {chunk["source_id"] for item in context.get("reviewed_prior", []) for chunk in item.get("chunks", [])}
    allowed |= {item["source_id"] for item in context.get("sources", [])
                if item.get("type") == "prior_photo" and item.get("included_in_request")}
    if set(sources) - allowed:
        raise ValueError("讲解引用了未审核、未提供或其他展点的资料")
    if segments and not sources:
        raise ValueError("讲解缺少先验资料来源，不能播报")
    data["segments"] = [item.strip() for item in segments]
    data["source_ids"] = list(dict.fromkeys(sources))
    data["needs_review"] = data["needs_review"] or bool(warnings)
    return data


def conceive_point(project=PROJECT, point_id=None, topic="", cancel=None, query=None, store=None):
    """Generate at call time; execution separately verifies physical arrival."""
    if not isinstance(topic, str) or len(topic) > 200:
        raise ValueError("讲解主题不能超过 200 字")
    cancel = cancel or threading.Event()
    if cancel.is_set():
        raise InterruptedError("讲解组织已取消")
    if not _LOCK.acquire(blocking=False):
        raise RuntimeError("已有 conception 正在进行，请稍后再试")
    try:
        store = store or KnowledgeStore(project=project)
        context = store.prepare_context(point_id)
        if cancel.is_set():
            raise InterruptedError("讲解组织已取消")
        image_b64 = None
        for source in context.get("sources", []):
            if source.get("type") == "prior_photo":
                path = store.points.image_path(point_id)
                tiles = prepare_tiles(base64.b64encode(path.read_bytes()).decode("ascii"))
                image_b64 = tiles[0]["image_jpeg_base64"]
                source["included_in_request"] = True
                break
        if not context.get("reviewed_prior") and not image_b64:
            raise ValueError("该导览点缺少可用先验，请先上传展板资料或审核文献")
        material = {"point": context["point"], "authority": "current_point_prior",
            "reviewed_prior": context.get("reviewed_prior", []),
            "sources": context.get("sources", []),
            "candidate_information": context.get("candidates", []),
            "live_observations": [], "topic": topic.strip(),
            "instructions": "按当前点先验组织讲解；旧摘要不是必须复述的台词；未审核候选及未解析PDF不得充当事实或引用。"}
        text = json.dumps(material, ensure_ascii=False)
        if len(text) > 60000:
            # Fail honestly instead of truncating sources and faking coverage.
            raise ValueError("该展点资料过多，请按主题整理文献后重试")
        payload = {"mode": "point_conception", "text": text}
        if image_b64:
            payload["image_jpeg_base64"] = image_b64
        if cancel.is_set():
            raise InterruptedError("讲解组织已取消")
        if query is None:
            python = os.environ.get("OMNI_PYTHON", str(Path(project) / "run/omni-venv/bin/python"))
            query = lambda request, timeout: run_board_omni(request, timeout, python, cancel)
        response = query(payload, 35)
        if cancel.is_set():
            raise InterruptedError("讲解组织已取消")
        if response.get("status") != "success" or response.get("actions") or response.get("tool_calls"):
            raise RuntimeError("conception 只生成讲解，禁止调用机器人工具")
        result = validate_presentation(response.get("text", ""), context)
        return {"status": "success", "point_id": point_id, "point_name": context["point"]["name"],
            "map_fingerprint": context["point"]["map_fingerprint"],
            "generated_at": time.time(), "presentation_mode": "dynamic_conception", **result}
    finally:
        _LOCK.release()
