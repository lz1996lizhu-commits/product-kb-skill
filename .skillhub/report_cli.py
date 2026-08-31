#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""SkillHub 使用情况上报 CLI（Python 版）.

供纯提示词型 skill 使用：这类 skill 没有代码执行入口，只能由 agent 按
SKILL.md 中的指令执行一条命令来完成上报。

设计约束：
  1. 不接受从命令行传入凭据。凭据由 report.py 自行从本地读取，
     绝不能出现在 agent 的上下文或会话日志里。
  2. 参数只认白名单，且逐项校验格式与长度。命令行的实际输入方是 agent，
     而 agent 的上下文可能被待处理的文件内容污染，因此这里按不可信输入处理：
     校验不通过的参数一律丢弃并打印原因，不做"尽力猜测"式的兼容。
  3. 无论成功失败都以 exit 0 退出，避免 agent 把上报失败误判为任务失败。
  4. 输出保持单行极简，避免污染 agent 上下文。

校验规则与 Node 版 report-cli.js 保持一致，改动需同步。

用法:
  python report_cli.py --quantity 50 --metric defect_count --unit 个
  python report_cli.py --remark "生成了报告"
"""

import argparse
import json
import re
import sys

from report import is_report_disabled, report_usage

# 备注最大长度。服务端上限为 1000，命令行入口收紧到 200，压缩可注入的文本量
MAX_REMARK_LENGTH = 200

# 扩展字段序列化后的最大长度
MAX_EXT_JSON_LENGTH = 1024

# 扩展字段允许的最大键数量
MAX_EXT_KEYS = 10

# 扩展字段中字符串值的最大长度
MAX_EXT_VALUE_LENGTH = 100

# 数量上限，超出视为参数异常而非真实业务量
MAX_QUANTITY = 1000000000

# Skill ID 上限，与服务端 bigint 对齐
MAX_SKILL_ID = 9223372036854775807

# 允许的调用端取值，比较时忽略大小写，命中后按此处的写法归一
CLIENT_TYPES = ("Kiro", "Qoder", "QoderWork", "WorkBuddy", "CI", "unknown")

# 禁止通过命令行传入的参数名，防止凭据与上报地址经过 agent 上下文
FORBIDDEN_ARGS = ("--credential", "--token", "--seed", "--secret",
                  "--password", "--base-url")

# 控制字符，出现在任何文本参数中都直接判为非法：可用于伪造日志行或注入终端转义序列
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")

# 指标标识：字母开头的短标识，用于聚合分组
METRIC_KEY_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.\-]{0,63}$")

# 版本号：数字字母加点划线
SKILL_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,19}$")

# 扩展字段的键名
EXT_KEY_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,31}$")


class ArgValueError(Exception):
    """参数取值不合法，捕获后丢弃该参数并打印原因."""


def _bounded_int(minimum, maximum):
    """构造整数校验器.

    :param minimum: 最小值
    :param maximum: 最大值
    :return: 校验函数
    """

    def convert(raw):
        text = raw.strip()
        if not re.match(r"^[+-]?\d{1,19}$", text):
            raise ArgValueError('取值 "%s" 不是整数' % raw)
        value = int(text)
        if value < minimum or value > maximum:
            raise ArgValueError("取值 %d 超出允许范围 [%d, %d]" % (value, minimum, maximum))
        return value

    return convert


def _plain_text(max_length):
    """构造受长度与字符集约束的文本校验器.

    :param max_length: 最大长度
    :return: 校验函数
    """

    def convert(raw):
        value = raw.strip()
        if not value:
            raise ArgValueError("取值为空")
        if len(value) > max_length:
            raise ArgValueError("长度 %d 超过上限 %d" % (len(value), max_length))
        if CONTROL_CHARS.search(value):
            raise ArgValueError("取值包含控制字符")
        return value

    return convert


def _patterned(regexp, hint):
    """构造正则约束的校验器.

    :param regexp: 允许的格式
    :param hint: 不匹配时的提示
    :return: 校验函数
    """

    def convert(raw):
        value = raw.strip()
        if not regexp.match(value):
            raise ArgValueError('取值 "%s" 不符合要求：%s' % (raw, hint))
        return value

    return convert


def _client_type(raw):
    """校验调用端：只接受白名单内的取值.

    :param raw: 原始取值
    :return: 归一后的调用端名称
    """
    value = raw.strip().lower()
    for item in CLIENT_TYPES:
        if item.lower() == value:
            return item
    raise ArgValueError('取值 "%s" 不在允许范围内：%s' % (raw, " / ".join(CLIENT_TYPES)))


def _ext_json(raw):
    """校验扩展字段：必须是扁平的 JSON 对象，键名与值都受限，通过后重新序列化.

    直接透传原始字符串等于把任意内容转发给服务端，因此这里解析后只保留
    通过校验的键值，再由本地重新序列化，杜绝原文夹带。

    :param raw: 原始 JSON 字符串
    :return: 规范化后的 JSON 字符串
    """
    try:
        parsed = json.loads(raw)
    except ValueError:
        raise ArgValueError("不是合法的 JSON")
    if not isinstance(parsed, dict):
        raise ArgValueError("必须是 JSON 对象")
    if len(parsed) > MAX_EXT_KEYS:
        raise ArgValueError("键数量 %d 超过上限 %d" % (len(parsed), MAX_EXT_KEYS))

    cleaned = {}
    for key, value in parsed.items():
        if not isinstance(key, str) or not EXT_KEY_PATTERN.match(key):
            raise ArgValueError('键名 "%s" 不合法，只允许字母开头的字母数字下划线组合' % key)
        if isinstance(value, str):
            if len(value) > MAX_EXT_VALUE_LENGTH:
                raise ArgValueError('键 "%s" 的取值长度超过 %d' % (key, MAX_EXT_VALUE_LENGTH))
            if CONTROL_CHARS.search(value):
                raise ArgValueError('键 "%s" 的取值包含控制字符' % key)
            cleaned[key] = value
        elif isinstance(value, bool) or value is None:
            cleaned[key] = value
        elif isinstance(value, (int, float)):
            cleaned[key] = value
        else:
            raise ArgValueError('键 "%s" 的取值类型不支持，只允许字符串、数字、布尔与 null' % key)

    serialized = json.dumps(cleaned, ensure_ascii=False, separators=(",", ":"))
    if len(serialized) > MAX_EXT_JSON_LENGTH:
        raise ArgValueError("序列化后长度 %d 超过上限 %d" % (len(serialized), MAX_EXT_JSON_LENGTH))
    return serialized


# 参数白名单：目标字段 -> (命令行名称元组, 校验器, 帮助文本)
ARG_SPECS = (
    ("quantity", ("--quantity", "-q"), _bounded_int(0, MAX_QUANTITY),
     "业务结果数量，0 ~ %d" % MAX_QUANTITY),
    ("metric_key", ("--metric", "-m"),
     _patterned(METRIC_KEY_PATTERN, "需为字母开头、不超过 64 字符的标识，如 defect_count"),
     "数量对应的指标标识（传 quantity 时必须一起传）"),
    ("metric_unit", ("--unit",), _plain_text(16), "数量单位，最长 16 字符，如 个 / 条"),
    ("remark", ("--remark", "-r"), _plain_text(MAX_REMARK_LENGTH),
     "备注，最长 %d 字符" % MAX_REMARK_LENGTH),
    ("skill_version", ("--version",),
     _patterned(SKILL_VERSION_PATTERN, "需为不超过 20 字符的版本号，如 1.0.0"),
     "使用的 skill 版本"),
    ("client_type", ("--client",), _client_type,
     "调用端，取值范围：%s" % " / ".join(CLIENT_TYPES)),
    ("skill_id", ("--skill-id",), _bounded_int(1, MAX_SKILL_ID),
     "Skill ID，正整数，默认从包内 .skillhub/seed 读取"),
    ("skill_name", ("--skill-name",), _plain_text(100),
     "Skill 标识名，最长 100 字符，默认从包内 .skillhub/seed 读取"),
    ("ext_json", ("--ext",), _ext_json,
     "扩展字段，扁平 JSON 对象，最多 %d 个键" % MAX_EXT_KEYS),
)


# 命令行名称到目标字段的索引
NAME_TO_DEST = {name: dest for dest, names, _v, _h in ARG_SPECS for name in names}


def _dedupe(argv):
    """同一字段重复传入时只保留第一次出现的取值.

    argparse 默认"最后一个生效"，重复传入不会有任何提示。这里显式收敛为
    "首次生效并告警"，与 Node 版行为一致，也避免后面的取值悄悄覆盖前面的。

    :param argv: 原始参数列表
    :return: (去重后的参数列表, 告警信息列表)
    """
    cleaned = []
    warnings = []
    seen = set()
    index = 0
    while index < len(argv):
        token = argv[index]
        name = token.split("=", 1)[0]
        dest = NAME_TO_DEST.get(name)
        # 本项的取值：形如 --key value 时取下一个 token，形如 --key=value 时已在 token 内
        value_tokens = []
        if dest is not None and "=" not in token and index + 1 < len(argv):
            value_tokens.append(argv[index + 1])

        if dest is not None and dest in seen:
            warnings.append("已忽略参数 %s：该参数重复传入，只取第一次的取值" % name)
        else:
            if dest is not None:
                seen.add(dest)
            cleaned.append(token)
            cleaned.extend(value_tokens)
        index += 1 + len(value_tokens)
    return cleaned, warnings


def _build_parser():
    """构建参数解析器.

    取值一律先按字符串收下，再由本模块的校验器处理。不把校验器交给 argparse 的
    type 参数，是因为 argparse 遇到 type 抛异常会直接退出进程并打印用法，
    而这里要求单个参数不合法时丢弃该参数、继续完成上报。
    """
    parser = argparse.ArgumentParser(
        prog="report_cli.py",
        description="SkillHub 使用情况上报。身份由本地凭据自动解析，无需通过命令行传入。"
                    "参数只认白名单并逐项校验，不合法的参数会被丢弃并打印原因。",
        add_help=True,
    )
    for dest, names, _validator, help_text in ARG_SPECS:
        parser.add_argument(*names, dest=dest, help=help_text)
    return parser


def _strip_forbidden(argv):
    """剔除凭据类参数及其取值.

    :param argv: 原始参数列表
    :return: (过滤后的参数列表, 告警信息列表)
    """
    cleaned = []
    warnings = []
    skip_next = False
    for token in argv:
        if skip_next:
            skip_next = False
            continue
        name = token.split("=", 1)[0].lower()
        if name in FORBIDDEN_ARGS:
            warnings.append("已忽略参数 %s：凭据与地址类信息不允许通过命令行传入" % name)
            skip_next = "=" not in token
            continue
        cleaned.append(token)
    return cleaned, warnings


def _validate(args):
    """逐项校验已解析的参数，丢弃不合法项.

    :param args: argparse 解析结果
    :return: (上报参数字典, 告警信息列表)
    """
    payload = {}
    warnings = []
    for dest, names, validator, _help_text in ARG_SPECS:
        raw = getattr(args, dest, None)
        if raw is None:
            continue
        try:
            payload[dest] = validator(raw)
        except ArgValueError as error:
            warnings.append("已忽略参数 %s：%s" % (names[0], error))
    return payload, warnings


def main():
    """入口，始终以 exit 0 结束."""
    try:
        argv, warnings = _strip_forbidden(sys.argv[1:])
        argv, dedupe_warnings = _dedupe(argv)
        warnings.extend(dedupe_warnings)

        parser = _build_parser()
        # 未知参数直接忽略，不让参数写错导致 agent 认为任务失败
        args, unknown = parser.parse_known_args(argv)
        for token in unknown:
            warnings.append("已忽略未知参数 %s" % token)

        payload, value_warnings = _validate(args)
        warnings.extend(value_warnings)
        # 先回显参数问题再判断开关：参数写错与是否上报是两件事，
        # 关闭上报时也应让调用方知道自己传错了参数
        for warning in warnings:
            print("[skillhub] %s" % warning)

        if is_report_disabled():
            print("[skillhub] 已按 SKILLHUB_DISABLE_REPORT 关闭上报，本次跳过")
            return

        # 传了数量却没给指标标识时提示，平台会归入 unspecified 桶
        if payload.get("quantity") is not None and not payload.get("metric_key"):
            print("[skillhub] 提示：传了 --quantity 建议同时传 --metric，否则数量无法按指标聚合")

        success = report_usage(**payload)
        print("[skillhub] 使用情况已上报" if success else "[skillhub] 使用情况上报未成功，已忽略")
    except SystemExit:
        # argparse 在 --help 或参数错误时会尝试非 0 退出，统一收敛为 0
        pass
    except Exception:  # noqa: BLE001 - 上报失败绝不影响 skill 主流程
        print("[skillhub] 使用情况上报异常，已忽略")
    sys.exit(0)


if __name__ == "__main__":
    main()
