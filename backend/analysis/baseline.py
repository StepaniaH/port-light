"""Deterministic explanations and a finite set of read-only inspection goals."""

import json

BASELINE_VERSION = "port-rules.v1"
MAX_INPUT_BYTES = 32 * 1024
CHECKS = {
    "scan_status": {
        "title": "查看扫描状态",
        "purpose": "核对来源是否启用、最近观察时间以及失败信息。",
        "changes_judgment": "来源未完成时，没有观察到占用不能当作空闲或停止。",
    },
    "mapping": {
        "title": "核对监听与 Docker 映射",
        "purpose": "在端口面板按 TCP/UDP 对照当前监听和实际映射。",
        "changes_judgment": "运行观察能与配置声明相互印证，但仍不能证明应用健康。",
    },
    "declaration": {
        "title": "查看 Compose 声明",
        "purpose": "在端口面板核对核心标记的冲突、协议与绑定范围。",
        "changes_judgment": "只有相同资源范围内的冲突证据，才能支持声明冲突判断。",
    },
    "binding": {
        "title": "核对绑定范围与地址族",
        "purpose": "分别查看 IPv4/IPv6 与本机、指定接口、所有接口的观察。",
        "changes_judgment": "绑定范围变化仅说明本机观察变化，不能确认公网可达。",
    },
    "port_history": {
        "title": "查看已记录变化",
        "purpose": "按事件时间核对前后状态、来源质量和历史缺口。",
        "changes_judgment": "有完整前后证据才可确认对应变化；记录时间不等于故障发生时间。",
    },
    "reservation": {
        "title": "核对预留与实际绑定",
        "purpose": "分别查看预留记录及当前监听，不把协调记录当作 OS 绑定结果。",
        "changes_judgment": "预留成功不保证服务启动，实际绑定结果需要另行核对。",
    },
}
STATUS = {"used": "占用", "configured": "配置声明", "free": "未观察到占用", "unknown": "未知"}
EVENT_NAMES = {
    "state_changed": "端口状态变化",
    "bind_scope_changed": "绑定范围变化",
    "configuration_mismatch": "配置关系变化",
    "observation_degraded": "观察质量下降",
    "observation_recovered": "观察质量恢复",
}
BIND_NAMES = {
    "public": "公共地址类别",
    "lan": "局域网类别",
    "link": "链路本地类别",
    "localhost": "本机类别",
    None: "未知",
}


def check(action, *evidence_ids):
    return {"action": action, "evidence_ids": list(evidence_ids), **CHECKS[action]}


def baseline(evidence, *, max_input_bytes=MAX_INPUT_BYTES):
    observation = evidence["observation"]
    entries = observation["current"]["entries"]
    scan = observation["scan"]
    events = observation["events"]
    question = evidence.get("question_type", "current_occupancy")
    observed = []
    for entry in entries:
        fields = []
        for flag, label in (
            ("listening", "监听"),
            ("docker_live", "运行中的 Docker 映射"),
            ("compose_declared", "Compose 声明"),
            ("manual", "手工记录"),
            ("reservation", "预留记录"),
        ):
            if entry[flag]:
                fields.append(label)
        text = (
            f"{entry['protocol'].upper()}：核心状态为{STATUS[entry['status']]}；"
            + ("、".join(fields) if fields else "没有记录到以上来源的占用")
            + "。"
        )
        if entry["compose_conflict"]:
            text += "核心标记了该协议下的声明冲突。"
        if entry["compose_relation"] == "declared_without_live_mapping":
            text += "有声明，未观察到对应的运行映射；这不能确认应用停止。"
        for binding in entry["bind"]:
            family = {"ipv4": "IPv4", "ipv6": "IPv6", "unknown": "未知地址族"}[binding["family"]]
            scope = {
                "all_interfaces": "所有接口",
                "loopback": "本机",
                "specific_interface": "指定接口",
                "unknown": "未知范围",
            }[binding["scope"]]
            text += f" {family} / {binding['source']}：{scope}。"
        observed.append(
            {"text": text, "evidence_ids": ["current"], "observed_at": observation["captured_at"]}
        )
    gaps = list(evidence["limitations"])
    checks = [check("scan_status", "scan")]
    reason, message = "available", "可选择模型解释这些事实；开始前仍需确认发送内容。"
    conclusion = {
        "code": "observed",
        "text": "已整理当前观察，请对照声明与运行事实。",
        "evidence_ids": ["current"],
    }
    if any(entry["compose_conflict"] for entry in entries):
        conclusion.update(
            code="configuration_conflict", text="核心标记了声明冲突，需要核对协议与范围。"
        )
        checks.append(check("declaration", "current"))
    elif any(entry["compose_relation"] == "declared_without_live_mapping" for entry in entries):
        conclusion.update(
            code="declaration_without_mapping", text="有配置声明，未观察到对应运行映射。"
        )
        checks.append(check("declaration", "current"))
    if any(entry["reservation"] for entry in entries):
        checks.append(check("reservation", "current"))
    elif any(entry["bind"] for entry in entries):
        checks.append(check("binding", "current"))
    else:
        checks.append(check("mapping", "current"))
    if question == "recorded_changes":
        changes = [
            event
            for event in events
            if event["kind"] in {"state_changed", "bind_scope_changed", "configuration_mismatch"}
        ]
        for event in events:
            before, after = event["before"], event["after"]
            text = EVENT_NAMES[event["kind"]]
            if event["kind"] == "state_changed":
                text += f"：{STATUS[before['status']]} → {STATUS[after['status']]}"
            elif event["kind"] == "bind_scope_changed":
                text += f"：{BIND_NAMES[before['bind_scope']]} → {BIND_NAMES[after['bind_scope']]}"
            elif event["kind"] == "configuration_mismatch":
                text += f"：冲突标记 {str(before['compose_conflict']).lower()} → {str(after['compose_conflict']).lower()}"
            observed.append(
                {
                    "text": text + "（端口级记录，以观察时间为准）。",
                    "observed_at": event["observed_at"],
                    "evidence_ids": [event["event_id"]],
                }
            )
        checks = [
            check("port_history", *[event["event_id"] for event in changes[:3]])
            if changes
            else check("port_history", "scan"),
            check("scan_status", "scan"),
            check("binding", "current"),
        ]
        if not changes:
            reason, message = (
                "no_recorded_changes",
                "所选窗口没有可解释的变化事件；不调用模型。可先检查记录是否启用。",
            )
            conclusion.update(
                code=reason,
                text="最近 24 小时的所选记录中没有可解释的变化。",
                evidence_ids=["scan"],
            )
        elif any(
            event["source_quality"] != "complete"
            or (
                event["kind"] == "bind_scope_changed"
                and (event["before"]["bind_scope"] is None or event["after"]["bind_scope"] is None)
            )
            for event in changes
        ):
            reason, message = (
                "incomplete_change",
                "变化事件的质量或前后证据不足，先核对记录；不调用模型猜测。",
            )
            conclusion.update(
                code=reason,
                text="变化记录存在证据缺口，不能确认完整变化。",
                evidence_ids=[event["event_id"] for event in changes[:3]],
            )
        else:
            conclusion.update(
                code="recorded_changes",
                text=f"所选窗口包含 {len(changes)} 个有前后记录的变化事件。",
                evidence_ids=[event["event_id"] for event in changes[:3]],
            )
    elif all(entry["status"] in {"free", "unknown"} for entry in entries):
        reason, message = "rules_sufficient", "当前问题可由观察与缺口说明回答，无需调用模型。"
        conclusion.update(code=reason, text="启用来源未观察到占用；这不是分配或启动保证。")
    if not scan["ready"] or not scan["complete"] or scan["stale"]:
        reason, message = "incomplete_scan", "扫描未完整就绪或观察已过期；先恢复采集，再重新预览。"
        conclusion.update(
            code=reason, text="观察不完整，不能据此确认空闲或停止。", evidence_ids=["scan"]
        )
        checks = [check("scan_status", "scan"), *checks[1:]]
    if len(json.dumps(evidence, ensure_ascii=False, sort_keys=True).encode()) > max_input_bytes:
        reason, message = "input_budget", "发送数据超过本地输入预算；请缩小历史范围后重新预览。"
    return {
        "version": BASELINE_VERSION,
        "question_type": question,
        "conclusion": conclusion,
        "observed": observed,
        "gaps": gaps,
        "checks": checks[:3],
        "ai": {"eligible": reason == "available", "reason": reason, "message": message},
    }
