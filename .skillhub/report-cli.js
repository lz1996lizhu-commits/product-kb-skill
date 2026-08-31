#!/usr/bin/env node

/**
 * SkillHub 使用情况上报 CLI（Node.js 版）
 *
 * 供纯提示词型 skill 使用：这类 skill 没有代码执行入口，只能由 agent 按
 * SKILL.md 中的指令执行一条命令来完成上报。
 *
 * 设计约束：
 *   1. 不接受从命令行传入凭据。凭据由 report.js 自行从本地读取，
 *      绝不能出现在 agent 的上下文或会话日志里。
 *   2. 参数只认白名单，且逐项校验格式与长度。命令行的实际输入方是 agent，
 *      而 agent 的上下文可能被待处理的文件内容污染，因此这里按不可信输入处理：
 *      校验不通过的参数一律丢弃并打印原因，不做"尽力猜测"式的兼容。
 *   3. 无论成功失败都以 exit 0 退出，避免 agent 把上报失败误判为任务失败。
 *   4. 输出保持单行极简，避免污染 agent 上下文。
 *
 * 用法:
 *   node report-cli.js --quantity 50 --metric defect_count --unit 个
 *   node report-cli.js --remark "生成了报告"
 */

'use strict';

const { reportUsage, isReportDisabled } = require('./report');

/** 备注最大长度。服务端上限为 1000，命令行入口收紧到 200，压缩可注入的文本量 */
const MAX_REMARK_LENGTH = 200;

/** 扩展字段序列化后的最大长度 */
const MAX_EXT_JSON_LENGTH = 1024;

/** 扩展字段允许的最大键数量 */
const MAX_EXT_KEYS = 10;

/** 扩展字段中字符串值的最大长度 */
const MAX_EXT_VALUE_LENGTH = 100;

/** 数量上限，超出视为参数异常而非真实业务量 */
const MAX_QUANTITY = 1000000000;

/** 允许的调用端取值，比较时忽略大小写，命中后按此处的写法归一 */
const CLIENT_TYPES = ['Kiro', 'Qoder', 'QoderWork', 'WorkBuddy', 'CI', 'unknown'];

/** 控制字符，出现在任何文本参数中都直接判为非法：可用于伪造日志行或注入终端转义序列 */
const CONTROL_CHARS = /[\u0000-\u001f\u007f]/;

/** 指标标识：字母开头的短标识，用于聚合分组 */
const METRIC_KEY_PATTERN = /^[A-Za-z][A-Za-z0-9_.-]{0,63}$/;

/** 版本号：数字字母加点划线 */
const SKILL_VERSION_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,19}$/;

/** 扩展字段的键名 */
const EXT_KEY_PATTERN = /^[A-Za-z][A-Za-z0-9_]{0,31}$/;

/**
 * 校验并规范化整数
 *
 * @param {string} raw 原始取值
 * @param {number} min 最小值
 * @param {number} max 最大值
 * @returns {{value: number}|{error: string}}
 */
function parseInteger(raw, min, max) {
  if (!/^[+-]?\d{1,19}$/.test(raw.trim())) {
    return { error: `取值 "${raw}" 不是整数` };
  }
  const parsed = Number(raw.trim());
  if (!Number.isSafeInteger(parsed)) {
    return { error: `取值 "${raw}" 超出可表示范围` };
  }
  if (parsed < min || parsed > max) {
    return { error: `取值 ${parsed} 超出允许范围 [${min}, ${max}]` };
  }
  return { value: parsed };
}

/**
 * 构造受长度与字符集约束的文本校验器
 *
 * @param {number} maxLength 最大长度
 * @returns {function(string): ({value: string}|{error: string})}
 */
function plainText(maxLength) {
  return (raw) => {
    const value = raw.trim();
    if (!value) {
      return { error: '取值为空' };
    }
    if (value.length > maxLength) {
      return { error: `长度 ${value.length} 超过上限 ${maxLength}` };
    }
    if (CONTROL_CHARS.test(value)) {
      return { error: '取值包含控制字符' };
    }
    return { value };
  };
}

/**
 * 构造正则约束的校验器
 *
 * @param {RegExp} regexp 允许的格式
 * @param {string} hint 不匹配时的提示
 * @returns {function(string): ({value: string}|{error: string})}
 */
function pattern(regexp, hint) {
  return (raw) => {
    const value = raw.trim();
    if (!regexp.test(value)) {
      return { error: `取值 "${raw}" 不符合要求：${hint}` };
    }
    return { value };
  };
}

/**
 * 校验扩展字段：必须是扁平的 JSON 对象，键名与值都受限，通过后按规范形式重新序列化
 *
 * 直接透传原始字符串等于把任意内容转发给服务端，因此这里解析后只保留
 * 通过校验的键值，再由本地重新序列化，杜绝原文夹带。
 *
 * @param {string} raw 原始 JSON 字符串
 * @returns {{value: string}|{error: string}}
 */
function parseExtJson(raw) {
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch (ignored) {
    return { error: '不是合法的 JSON' };
  }
  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return { error: '必须是 JSON 对象' };
  }

  const keys = Object.keys(parsed);
  if (keys.length > MAX_EXT_KEYS) {
    return { error: `键数量 ${keys.length} 超过上限 ${MAX_EXT_KEYS}` };
  }

  const cleaned = {};
  for (const key of keys) {
    if (!EXT_KEY_PATTERN.test(key)) {
      return { error: `键名 "${key}" 不合法，只允许字母开头的字母数字下划线组合` };
    }
    const value = parsed[key];
    if (typeof value === 'string') {
      if (value.length > MAX_EXT_VALUE_LENGTH) {
        return { error: `键 "${key}" 的取值长度超过 ${MAX_EXT_VALUE_LENGTH}` };
      }
      if (CONTROL_CHARS.test(value)) {
        return { error: `键 "${key}" 的取值包含控制字符` };
      }
      cleaned[key] = value;
    } else if (typeof value === 'number') {
      if (!Number.isFinite(value)) {
        return { error: `键 "${key}" 的取值不是有限数字` };
      }
      cleaned[key] = value;
    } else if (typeof value === 'boolean' || value === null) {
      cleaned[key] = value;
    } else {
      return { error: `键 "${key}" 的取值类型不支持，只允许字符串、数字、布尔与 null` };
    }
  }

  const serialized = JSON.stringify(cleaned);
  if (serialized.length > MAX_EXT_JSON_LENGTH) {
    return { error: `序列化后长度 ${serialized.length} 超过上限 ${MAX_EXT_JSON_LENGTH}` };
  }
  return { value: serialized };
}

/**
 * 校验调用端：只接受白名单内的取值
 *
 * @param {string} raw 原始取值
 * @returns {{value: string}|{error: string}}
 */
function parseClientType(raw) {
  const value = raw.trim();
  const matched = CLIENT_TYPES.find((item) => item.toLowerCase() === value.toLowerCase());
  if (!matched) {
    return { error: `取值 "${raw}" 不在允许范围内：${CLIENT_TYPES.join(' / ')}` };
  }
  return { value: matched };
}

/**
 * 参数白名单：字段名、别名与校验器
 *
 * 校验器返回 {value} 表示通过，返回 {error} 表示丢弃该参数并打印原因。
 */
const ARG_SPECS = [
  {
    field: 'quantity',
    names: ['quantity', 'q'],
    validate: (raw) => parseInteger(raw, 0, MAX_QUANTITY),
  },
  {
    field: 'metricKey',
    names: ['metric', 'metric-key', 'm'],
    validate: pattern(METRIC_KEY_PATTERN, '需为字母开头、不超过 64 字符的标识，如 defect_count'),
  },
  {
    field: 'metricUnit',
    names: ['unit', 'metric-unit'],
    validate: plainText(16),
  },
  {
    field: 'remark',
    names: ['remark', 'r'],
    validate: plainText(MAX_REMARK_LENGTH),
  },
  {
    field: 'skillVersion',
    names: ['version', 'skill-version'],
    validate: pattern(SKILL_VERSION_PATTERN, '需为不超过 20 字符的版本号，如 1.0.0'),
  },
  {
    field: 'clientType',
    names: ['client', 'client-type'],
    validate: parseClientType,
  },
  {
    field: 'skillId',
    names: ['skill-id'],
    validate: (raw) => parseInteger(raw, 1, Number.MAX_SAFE_INTEGER),
  },
  {
    field: 'skillName',
    names: ['skill-name'],
    validate: plainText(100),
  },
  {
    field: 'extJson',
    names: ['ext', 'ext-json'],
    validate: parseExtJson,
  },
];

/** 参数名到规格的索引 */
const SPEC_BY_NAME = new Map();
ARG_SPECS.forEach((spec) => spec.names.forEach((name) => SPEC_BY_NAME.set(name, spec)));

/** 禁止通过命令行传入的参数名，防止凭据经过 agent 上下文 */
const FORBIDDEN_ARGS = new Set(['credential', 'token', 'seed', 'secret', 'password', 'base-url']);

/** 帮助信息 */
const HELP_TEXT = `SkillHub 使用情况上报

用法:
  node report-cli.js [选项]

选项:
  --quantity, -q <整数>   业务结果数量，0 ~ ${MAX_QUANTITY}
  --metric, -m <标识>     数量对应的指标标识，字母开头，如 defect_count（传 quantity 时必须一起传）
  --unit <单位>           数量单位，最长 16 字符，如 个 / 条
  --remark, -r <文本>     备注，最长 ${MAX_REMARK_LENGTH} 字符
  --version <版本号>      使用的 skill 版本，如 1.0.0
  --client <调用端>       取值范围：${CLIENT_TYPES.join(' / ')}
  --skill-id <ID>         Skill ID，正整数，默认从包内 .skillhub/seed 读取
  --skill-name <名称>     Skill 标识名，最长 100 字符，默认从包内 .skillhub/seed 读取
  --ext <JSON字符串>      扩展字段，扁平 JSON 对象，最多 ${MAX_EXT_KEYS} 个键
  --help, -h              显示帮助

说明:
  身份由本地凭据自动解析，无需也无法通过命令行传入。
  参数只认上述白名单，且逐项校验；不合法的参数会被丢弃并打印原因。
  命令始终以 exit 0 退出，上报失败不会影响调用方。
  设置环境变量 SKILLHUB_DISABLE_REPORT=1 可关闭上报。
`;

/**
 * 解析并校验命令行参数
 *
 * 同时支持 --key value 与 --key=value 两种写法
 *
 * @param {string[]} argv 参数数组
 * @returns {{options: object, help: boolean, warnings: string[]}}
 */
function parseArgs(argv) {
  const options = {};
  const warnings = [];
  let help = false;

  for (let i = 0; i < argv.length; i += 1) {
    const token = argv[i];
    if (token !== '--' && !token.startsWith('-')) {
      continue;
    }

    let name = token.replace(/^--?/, '');
    let value = null;

    const equalIndex = name.indexOf('=');
    if (equalIndex > -1) {
      value = name.slice(equalIndex + 1);
      name = name.slice(0, equalIndex);
    }
    name = name.toLowerCase();

    if (name === 'help' || name === 'h') {
      help = true;
      continue;
    }

    if (FORBIDDEN_ARGS.has(name)) {
      warnings.push(`已忽略参数 --${name}：凭据与地址类信息不允许通过命令行传入`);
      // 同时跳过其后紧跟的值，避免被当成下一个参数处理
      if (value === null && i + 1 < argv.length && !argv[i + 1].startsWith('-')) {
        i += 1;
      }
      continue;
    }

    const spec = SPEC_BY_NAME.get(name);
    if (!spec) {
      warnings.push(`已忽略未知参数 --${name}`);
      continue;
    }

    if (value === null) {
      if (i + 1 >= argv.length || argv[i + 1].startsWith('-')) {
        warnings.push(`已忽略参数 --${name}：缺少取值`);
        continue;
      }
      i += 1;
      value = argv[i];
    }

    if (Object.prototype.hasOwnProperty.call(options, spec.field)) {
      warnings.push(`已忽略参数 --${name}：该参数重复传入，只取第一次的取值`);
      continue;
    }

    const result = spec.validate(value);
    if (result.error) {
      warnings.push(`已忽略参数 --${name}：${result.error}`);
      continue;
    }
    options[spec.field] = result.value;
  }

  return { options, help, warnings };
}

/**
 * 入口
 */
async function main() {
  const { options, help, warnings } = parseArgs(process.argv.slice(2));

  if (help) {
    process.stdout.write(HELP_TEXT);
    return;
  }

  // 先回显参数问题再判断开关：参数写错与是否上报是两件事，
  // 关闭上报时也应让调用方知道自己传错了参数
  warnings.forEach((warning) => process.stdout.write(`[skillhub] ${warning}\n`));

  if (isReportDisabled()) {
    process.stdout.write('[skillhub] 已按 SKILLHUB_DISABLE_REPORT 关闭上报，本次跳过\n');
    return;
  }

  // 传了数量却没给指标标识时提示，平台会归入 unspecified 桶
  if (options.quantity != null && !options.metricKey) {
    process.stdout.write('[skillhub] 提示：传了 --quantity 建议同时传 --metric，否则数量无法按指标聚合\n');
  }

  const success = await reportUsage(options);
  process.stdout.write(success ? '[skillhub] 使用情况已上报\n' : '[skillhub] 使用情况上报未成功，已忽略\n');
}

// 无论成功失败都以 exit 0 退出，确保上报不影响 skill 主流程
main()
  .catch(() => {
    process.stdout.write('[skillhub] 使用情况上报异常，已忽略\n');
  })
  .finally(() => {
    process.exitCode = 0;
  });
