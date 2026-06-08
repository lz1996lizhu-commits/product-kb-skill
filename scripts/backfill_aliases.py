#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backfill_aliases.py
===================

为高频核心条目填入精选 aliases（同义词 / 缩写 / 口语词 / 双语别名）。

设计原则
--------
- 精选优先：仅针对真正能提升召回率的条目（约 40 个），其余条目保持 aliases: []
  避免无价值的关键词膨胀污染倒排索引
- 与 tags 互补：alias 不能与该条目现有 tag 重复（重复无意义，rebuild_index.sh 会自动 sort -u）
- 幂等：通过比较新旧 aliases 集合，相同时跳过文件
- 仅识别内联 `aliases: [...]` 单行格式（与 backfill_cloud_field.py 写入的格式一致）

用法
----
  backfill_aliases.py             # 实际写入
  backfill_aliases.py --dry-run   # 预览
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

KB_PATH = Path(os.environ.get("KB_PATH", str(Path.home() / ".product-knowledge-base")))
KNOWLEDGE_DIR = KB_PATH / "knowledge"

# ============================================================
# 精选 aliases 字典：path-relative-to-knowledge → [aliases]
# ============================================================
ALIASES: dict[str, list[str]] = {
    # ===== 人才发展云：干部 =====
    "product/feature-cadre-management.md": ["干部任免", "干部梯队", "干部管理方案"],
    "guide/guide-cadre-term.md": ["任期管理", "干部任期管理", "任期生成"],
    "guide/guide-cadre-appointment-nomination.md": ["任命", "提名"],
    "guide/guide-cadre-deliberation.md": ["动议"],
    "guide/guide-cadre-dismissal.md": ["免职", "解聘"],
    "guide/guide-cadre-investigation.md": ["干部考察"],
    "guide/guide-cadre-publication.md": ["签发", "发布"],
    "guide/guide-cadre-publicity.md": ["干部公示"],
    "guide/guide-cadre-qualification-review.md": ["资格审查", "资审"],

    # ===== 人才发展云：职级评定 =====
    "product/feature-job-level-overview.md": ["职级管理", "职级体系", "晋升体系"],
    "product/feature-job-level-application.md": ["职级申报", "晋升申报", "职级申请"],
    "product/feature-job-level-application-materials.md": ["申报材料", "晋升材料"],
    "product/feature-job-level-application-qualification.md": ["申报资格", "晋升资格"],
    "product/feature-job-level-document-review.md": ["资料复核", "材料审核"],
    "product/feature-job-level-result-approval.md": ["结果报批", "结果审批", "晋升结果审批"],
    "product/feature-job-level-result-resolution.md": ["结果决议", "决议"],
    "product/feature-job-level-review-arrangement.md": ["评审安排", "答辩安排"],
    "product/feature-job-level-review-evaluation.md": ["评审评议", "答辩评议"],
    "product/feature-job-level-review-method.md": ["评审方式", "答辩方式"],
    "product/feature-job-level-promotion-nomination.md": ["晋升提名", "晋升推荐"],
    "product/feature-job-level-list-confirmation.md": ["名单确认"],
    "product/feature-job-level-batch-adjustment.md": ["批量调整", "批量晋升"],
    "product/feature-job-level-approval-management.md": ["报批管理", "审批管理"],
    "product/feature-job-level-evaluation-form.md": ["评估表", "评审表"],
    "product/feature-job-level-evaluation-plan.md": ["评定计划", "评审计划"],
    "product/feature-job-level-standard.md": ["职级标准", "评定标准"],

    # ===== 人才发展云：任职资格 =====
    "business/process-qualification.md": ["任职资格认证", "资格认证流程"],

    # ===== 人才发展云：人才盘点 =====
    "product/feature-talent-inventory-overview.md":
        ["人才盘点", "人才九宫格", "9宫格", "12宫格", "九宫格盘点"],
    "product/feature-talent-inventory-plan.md": ["盘点计划", "盘点活动计划"],
    "product/feature-talent-inventory-template.md": ["盘点模板"],
    "product/feature-talent-inventory-evaluation-item.md": ["盘点评估项", "盘点评估指标"],
    "product/feature-talent-inventory-evaluation-scheme.md": ["盘点评估方案"],
    "guide/guide-talent-inventory.md": ["盘点活动", "人才盘点指南"],
    "guide/guide-talent-inventory-calibration.md": ["盘点校准", "在线校准"],
    "guide/guide-talent-inventory-grouping.md": ["盘点分组"],

    # ===== 人才发展云：人才档案 =====
    "product/feature-talent-archive-overview.md": ["人才档案", "员工档案"],
    "product/feature-talent-archive-maintenance.md": ["档案维护"],
    "product/feature-talent-archive-change.md": ["档案变动", "档案变更"],
    "product/feature-talent-archive-collaboration.md": ["档案协同"],

    # ===== 人才发展云：能力素质 / 专委会 / 认证组 =====
    "product/feature-competency-management.md":
        ["能力素质", "胜任力", "胜任力模型", "能力模型", "Competency"],
    "product/feature-certification-group.md": ["认证组", "评审组", "评定组"],
    "product/feature-committee-architecture.md": ["专委会架构", "专业委员会"],
    "product/feature-committee-roles.md": ["评委", "委员", "专委会成员"],

    # ===== 人才发展云：人才发展基础 / 人才星图 / 人才储备池 =====
    "product/feature-talent-development.md": ["人才发展整体介绍"],
    "product/feature-talent-dev-foundation.md": ["人才发展基础服务", "TD 基础服务"],

    # ===== 目标绩效云：绩效核心 =====
    "product/feature-performance.md": ["绩效模块", "绩效"],
    "product/feature-performance-overview.md": ["绩效管理", "绩效考核"],
    "product/feature-performance-plan.md": ["考核计划", "绩效计划"],
    "product/feature-performance-plan-overview.md": ["考核计划整体介绍"],
    "product/feature-performance-archive.md": ["绩效档案", "个人绩效档案"],
    "product/feature-performance-archive-overview.md": ["绩效档案整体介绍"],
    "product/feature-performance-evaluation.md": ["绩效评估", "考核评估", "打分"],
    "product/feature-performance-interview.md": ["绩效面谈", "1on1", "一对一", "考核面谈"],
    "product/feature-performance-result.md": ["绩效结果", "考核结果"],
    "product/feature-result-summary.md": ["结果汇总"],
    "product/feature-result-confirmation.md": ["结果确认", "员工确认"],
    "product/feature-assessment-completion.md": ["考核完成", "结果归档", "绩效归档"],
    "product/feature-my-performance.md": ["我的绩效", "员工绩效门户"],
    "product/feature-evaluation-form.md": ["考核表", "评估表", "考核表单", "KPI", "BSC", "PBC"],
    "product/feature-evaluation-object-mgmt.md": ["评估对象", "考核对象"],
    "product/feature-indicator-setting.md": ["指标制定", "KPI 设定", "绩效指标"],
    "product/feature-scoring-scale.md": ["计分规则", "评分尺", "等级方案"],

    # ===== 目标绩效云：员工变动协作 =====
    "product/feature-employee-change-overview.md": ["员工变动协作", "变动协同"],
    "product/feature-employee-change-processing.md": ["员工变动处理", "变动处理"],
    "product/feature-employee-collaboration-config.md": ["员工协作配置"],

    # ===== 目标绩效云：业务流程 =====
    "business/process-performance-review.md": ["绩效考核流程", "绩效流程"],

    # ===== 目标绩效云：guide =====
    "guide/guide-evaluation-form.md": ["考核表配置", "评估表配置"],
    "guide/guide-performance-indicators.md": ["KPI 配置", "指标配置"],
    "guide/guide-performance-process.md": ["绩效流程配置"],
    "guide/guide-grade-rule.md": ["等级规则"],
    "guide/guide-grade-ruler.md": ["等级尺"],
    "guide/guide-scoring-scale.md": ["计分规则配置"],
    "guide/guide-role-type.md": ["角色类型"],

    # ===== 人才供应云：招聘核心 =====
    "product/feature-recruitment-express.md": ["招聘服务直通车", "招聘直通车", "直通车"],
    "product/feature-recruitment-foundation.md": ["招聘基础服务"],
    "product/feature-internal-recruitment.md": ["内部招聘", "内招", "内部人才流动"],
    "product/feature-talent-supply.md": ["人才供应云", "招聘"],

    # ===== 人才供应云：招聘服务直通车 =====
    "product/feature-express-candidate.md": ["候选人", "应聘者", "求职者", "Candidate"],
    "product/feature-express-position.md": ["招聘职位", "岗位"],
    "product/feature-express-hire-application.md": ["录用申请", "录用审批"],
    "product/feature-express-hire-notice.md": ["录用通知", "录用消息"],
    "product/feature-express-offer-letter.md":
        ["Offer Letter", "Offer", "录用通知书", "聘书"],
    "product/feature-express-onboard-application.md": ["入职协同", "入职申请"],
    "product/feature-express-eco-tenant.md": ["生态租户", "第三方招聘系统"],

    # ===== 人才供应云：内部招聘 =====
    "product/feature-ir-position.md": ["内部招聘职位"],
    "product/feature-ir-candidate.md": ["内部招聘候选人"],
    "product/feature-ir-interview.md": ["面试"],
    "product/feature-ir-hire-application.md": ["内部录用申请"],
    "product/feature-ir-apply-permission.md": ["应聘许可"],
    "product/feature-ir-advertisement.md": ["招聘广告", "渠道广告"],
    "product/feature-interviewer-portal.md": ["面试官工作台", "人人面试官"],

    # ===== 人才供应云：业务流程 / guide =====
    "business/process-internal-recruitment.md": ["内部招聘流程", "内招流程"],
    "business/process-external-recruitment.md": ["外部招聘流程", "外招流程"],
    "guide/guide-offer-letter-template.md": ["Offer 模板", "录用通知书模板"],
    "guide/guide-message-template.md": ["消息模板"],
    "guide/guide-moka-integration.md": ["Moka 对接", "第三方招聘对接"],
    "guide/guide-interview-evaluation-form.md": ["面试评估表"],
    "guide/guide-interviewer.md": ["面试官管理"],
    "guide/guide-recruitment-channel.md": ["招聘渠道"],
    "guide/guide-recruitment-process-config.md": ["招聘流程配置"],
    "guide/guide-apply-progress-config.md": ["应聘进展配置"],
    "guide/guide-express-params-config.md": ["直通车参数配置"],
    "guide/guide-ir-params-config.md": ["内部招聘参数配置"],
    "guide/guide-integration-external-recruitment.md": ["外部招聘集成"],

    # ===== FAQ =====
    "faq/faq-product-overview.md": ["产品 FAQ", "产品架构 FAQ"],
    "faq/faq-performance-terminology.md": ["绩效术语"],
    "faq/faq-ir-terminology.md": ["内部招聘术语"],
    "faq/faq-express-terminology.md": ["直通车术语"],
    "faq/faq-job-level-no-add-button.md": ["职级申报无新增按钮"],
    "faq/faq-message-template-variables.md": ["消息模板变量"],
    "faq/faq-switch-salary-param-by-field.md": ["定薪字段切换"],
}


def get_existing_tags(fm_lines: list[str]) -> set[str]:
    """提取已有 tags（lower），用于去重避免 alias 与 tag 重复。"""
    tags: set[str] = set()
    for i, ln in enumerate(fm_lines):
        m = re.match(r"^tags\s*:\s*\[(.*)\]\s*$", ln)
        if m:
            for t in m.group(1).split(","):
                t = t.strip().strip('"').strip("'").lower()
                if t:
                    tags.add(t)
            return tags
        if re.match(r"^tags\s*:\s*$", ln):
            j = i + 1
            while j < len(fm_lines) and fm_lines[j].lstrip().startswith("- "):
                t = fm_lines[j].lstrip()[2:].strip().strip('"').strip("'").lower()
                if t:
                    tags.add(t)
                j += 1
            return tags
    return tags


def parse_inline_aliases(line: str) -> list[str] | None:
    """解析 `aliases: [a, b, c]` 内联格式，返回 list；非内联返回 None。"""
    m = re.match(r"^aliases\s*:\s*\[(.*)\]\s*$", line)
    if not m:
        return None
    inner = m.group(1).strip()
    if inner == "":
        return []
    return [
        x.strip().strip('"').strip("'")
        for x in inner.split(",")
        if x.strip()
    ]


def render_aliases_inline(items: list[str]) -> str:
    if not items:
        return "aliases: []\n"
    # 用 [a, b, c] 形式；中文不加引号；含逗号或冒号的特殊词加引号（保守起见）
    parts = []
    for x in items:
        if any(c in x for c in [",", ":", "[", "]"]):
            parts.append(f'"{x}"')
        else:
            parts.append(x)
    return f"aliases: [{', '.join(parts)}]\n"


def split_frontmatter(text: str):
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return None
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return None
    return ([lines[0]], lines[1:end], lines[end:])


def process_file(path: Path, target_aliases: list[str], dry_run: bool) -> dict:
    text = path.read_text(encoding="utf-8")
    parts = split_frontmatter(text)
    if parts is None:
        return {"path": path, "status": "no-frontmatter"}
    head, fm, tail = parts

    existing_tags = get_existing_tags(fm)

    # 与 tags 重复的 alias 剔除（lower 比较）
    deduped = []
    seen = set()
    for a in target_aliases:
        key = a.lower()
        if key in existing_tags:
            continue
        if key in seen:
            continue
        seen.add(key)
        deduped.append(a)

    # 找到 aliases 行（仅识别内联格式）
    alias_idx = None
    current = None
    for i, ln in enumerate(fm):
        v = parse_inline_aliases(ln)
        if v is not None:
            alias_idx = i
            current = v
            break

    if alias_idx is None:
        # 没有 aliases 行（理论上 backfill_cloud_field.py 应已写入；这里兜底）
        # 在末尾追加
        new_line = render_aliases_inline(deduped)
        new_fm = list(fm)
        if new_fm and not new_fm[-1].endswith("\n"):
            new_fm[-1] = new_fm[-1] + "\n"
        new_fm.append(new_line)
        action = "appended"
    else:
        # 幂等：当前 aliases 与目标完全一致则跳过
        if [x.lower() for x in current] == [x.lower() for x in deduped]:
            return {"path": path, "status": "skip-same"}
        new_fm = list(fm)
        new_fm[alias_idx] = render_aliases_inline(deduped)
        action = "rewritten"

    new_text = "".join(head + new_fm + tail)
    if not dry_run:
        path.write_text(new_text, encoding="utf-8")
    return {
        "path": path,
        "status": "patched",
        "action": action,
        "aliases": deduped,
        "filtered_dup": len(target_aliases) - len(deduped),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not KNOWLEDGE_DIR.is_dir():
        print(f"❌ 知识库目录不存在: {KNOWLEDGE_DIR}", file=sys.stderr)
        return 1

    print(f"📂 目标条目: {len(ALIASES)} 个（其余 287-N 条目保持 aliases: []）")
    summary = {"patched": 0, "skip-same": 0, "no-frontmatter": 0, "missing": 0}
    total_aliases = 0
    total_dup_filtered = 0

    for rel, aliases in ALIASES.items():
        f = KNOWLEDGE_DIR / rel
        if not f.is_file():
            print(f"  ⚠️  缺失: {rel}")
            summary["missing"] += 1
            continue
        r = process_file(f, aliases, args.dry_run)
        summary[r["status"]] = summary.get(r["status"], 0) + 1
        if r["status"] == "patched":
            total_aliases += len(r["aliases"])
            total_dup_filtered += r["filtered_dup"]
            tag = "+" if r["action"] == "appended" else "~"
            print(f"  [{tag}] {rel}  → {r['aliases']}")
            if r["filtered_dup"]:
                print(f"      （去重过滤 {r['filtered_dup']} 个与 tags 重复的 alias）")

    print()
    print("📊 处理结果：")
    for k, v in summary.items():
        if v:
            print(f"   {k}: {v}")
    if summary.get("patched"):
        print(f"   写入 alias 总数: {total_aliases}")
        if total_dup_filtered:
            print(f"   去重过滤的 alias: {total_dup_filtered}")
    if args.dry_run:
        print()
        print("📋 --dry-run 模式，未写入文件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
