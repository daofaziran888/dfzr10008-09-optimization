"""产品版本管理业务规则：状态流转、兼容检查、灰度冲突裁决与发布幂等都收在这里。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

MODULE = "version"
REQUIRED_FIELDS = ["版本编号", "产品名称", "版本号"]
OPTIONAL_FIELDS = ["变更日志", "兼容标识", "灰度比例", "灰度规则", "全量时间", "版本状态"]
STATUS_ORDER = ["草稿", "灰度中", "全量发布", "已归档"]
# 动作 -> (要求的当前状态, 目标状态)：已归档不在任何目标里，归档后只读。
ACTION_FLOW = {"灰度发布": ("草稿", "灰度中"), "全量发布": ("灰度中", "全量发布"), "归档版本": ("全量发布", "已归档")}
ARCHIVED_STATUS = "已归档"
RELEASED_STATUS = "全量发布"

# 兼容检查失败原因编码：空兼容标识 / 异常灰度范围 / 非法灰度比例，分别说明。
COMPAT_EMPTY = "COMPAT-EMPTY"
COMPAT_RANGE = "COMPAT-RANGE"
COMPAT_RATIO = "COMPAT-RATIO"


def _parse_ratio(raw: Any) -> float | None:
    """把「10%」「10」、10 这类写法解析成百分比数值；非法或超出 1~100 返回 None。"""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        value = float(raw)
    else:
        text = str(raw).strip().rstrip("%").strip()
        if not text:
            return None
        try:
            value = float(text)
        except ValueError:
            return None
    if value <= 0 or value > 100:
        return None
    return value


def _normalize_rules(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """取出灰度规则列表；没有配置「灰度规则」时退回用「灰度比例」字段拼一条默认规则。"""
    rules = entry.get("灰度规则")
    if isinstance(rules, list):
        return [dict(rule) for rule in rules if isinstance(rule, dict)]
    ratio = str(entry.get("灰度比例") or "").strip()
    if ratio:
        return [{"来源渠道": "默认渠道", "灰度比例": ratio, "规则版本": 1}]
    return []


def _compat_check(entry: dict[str, Any]) -> tuple[list[dict[str, Any]] | None, str | None]:
    """全量发布前的兼容检查。通过时返回 (规则列表, None)，失败返回 (None, 原因)。"""
    compat = str(entry.get("兼容标识") or "").strip()
    if not compat:
        return None, (
            f"兼容检查未通过 [{COMPAT_EMPTY}]：兼容标识为空，缺失项：兼容标识；"
            "暂无有效兼容快照，版本保持原状态，补齐兼容标识后可重试"
        )
    rules = _normalize_rules(entry)
    if not rules:
        return None, (
            f"兼容检查未通过 [{COMPAT_RANGE}]：灰度范围异常，未配置任何灰度规则；"
            "暂无有效兼容快照，版本保持原状态，补充灰度规则后可重试"
        )
    for rule in rules:
        if _parse_ratio(rule.get("灰度比例")) is None:
            return None, (
                f"兼容检查未通过 [{COMPAT_RATIO}]：灰度比例「{rule.get('灰度比例')}」非法，"
                "需为 1~100 的百分比；暂无有效兼容快照，版本保持原状态，修正后可重试"
            )
    return rules, None


def _resolve_rules(rules: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """灰度范围冲突裁决：适用范围更窄（比例更小）的规则优先，比例相同取规则版本更高的一条。"""
    def sort_key(rule: dict[str, Any]) -> tuple[float, int]:
        ratio = _parse_ratio(rule.get("灰度比例")) or 0.0
        try:
            rule_version = int(rule.get("规则版本") or 0)
        except (TypeError, ValueError):
            rule_version = 0
        return (ratio, -rule_version)

    ordered = sorted(rules, key=sort_key)
    return ordered[0], ordered[1:]


def _next_snapshot_id() -> str:
    """生成全局唯一的兼容快照编号：扫描已用编号后取下一个空位。"""
    used = set()
    for row in store.rows(MODULE):
        snapshot = row.get("兼容快照")
        if isinstance(snapshot, dict):
            used.add(str(snapshot.get("快照编号") or ""))
    seq = 1
    while f"SNAP-{seq:04d}" in used:
        seq += 1
    return f"SNAP-{seq:04d}"


def _release_message(snapshot: dict[str, Any]) -> str:
    adopted = snapshot["采用规则"]
    parts = [
        f"产品版本已全量发布，兼容快照 {snapshot['快照编号']}："
        f"采用 {adopted.get('来源渠道')} {adopted.get('灰度比例')}（规则版本 {adopted.get('规则版本')}）"
    ]
    for rule in snapshot["忽略规则"]:
        parts.append(f"忽略 {rule.get('来源渠道')} {rule.get('灰度比例')}（规则版本 {rule.get('规则版本')}）")
    return "，".join(parts)


class VersionService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("版本编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        for field in OPTIONAL_FIELDS:
            if field in values and values.get(field) not in (None, ""):
                entry[field] = values.get(field)
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"产品版本 {entry_id} 不存在或已归档"
        if action not in ACTION_FLOW:
            return None, f"动作「{action}」不属于产品版本管理可执行范围"
        if entry.get("status") == ARCHIVED_STATUS:
            return None, (
                f"产品版本 {entry.get('版本编号') or entry_id} 已归档，版本只读，"
                f"「{action}」不会改变其状态"
            )
        # 幂等：同一全量请求重复提交时，直接返回现有版本发布结果，不生成新快照。
        if action == "全量发布" and entry.get("status") == RELEASED_STATUS and isinstance(entry.get("兼容快照"), dict):
            snapshot = entry["兼容快照"]
            return entry, f"产品版本已全量发布，重复提交返回现有发布结果（兼容快照 {snapshot.get('快照编号')}）"
        expected, target = ACTION_FLOW[action]
        current = str(entry.get("status") or "")
        if current != expected:
            return None, f"产品版本当前状态为「{current}」，「{action}」要求版本处于「{expected}」，状态保持不变"
        if action == "全量发布":
            rules, error = _compat_check(entry)
            if error is not None or rules is None:
                return None, error or "兼容检查未通过，版本保持原状态"
            adopted, ignored = _resolve_rules(rules)
            snapshot = {
                "快照编号": _next_snapshot_id(),
                "版本编号": entry.get("版本编号"),
                "兼容标识": str(entry.get("兼容标识") or "").strip(),
                "采用规则": adopted,
                "忽略规则": ignored,
                "生成时间": datetime.now().isoformat(timespec="seconds"),
            }
            entry["兼容快照"] = snapshot
            entry["全量时间"] = snapshot["生成时间"][:10]
            message = _release_message(snapshot)
        else:
            message = "产品版本已归档" if action == "归档版本" else f"产品版本已{action}"
        entry["status"] = target
        entry["版本状态"] = target
        entry["pending"] = target in ("草稿", "灰度中")
        entry["abnormal"] = False
        return entry, message
