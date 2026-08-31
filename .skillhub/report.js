/**
 * SkillHub 使用情况上报客户端（Node.js 版）
 *
 * 直接复制到 skill 目录中使用。封装了：
 *   - 凭据优先级读取：环境变量 > ~/.skillhub/credential > 包内 .skillhub/seed（首次绑定）
 *   - 首次运行用种子换取长期凭据并落盘
 *   - 幂等 requestId 生成
 *   - 超时与静默重试
 *
 * 铁律：上报失败绝不影响 skill 主流程，所有异常在内部吞掉。
 */

'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const crypto = require('crypto');

/** 平台地址兜底值：包内种子没带地址、也没配环境变量时使用 */
const DEFAULT_BASE_URL = 'https://skills.kingdee.com';

/** 关闭上报的环境变量名，取值 1/true/yes 时不发送任何请求 */
const DISABLE_ENV_NAME = 'SKILLHUB_DISABLE_REPORT';

/** 长期凭据保存路径，所有 skill 共用 */
const CREDENTIAL_FILE = path.join(os.homedir(), '.skillhub', 'credential');

/** 兜底配置（工号）保存路径 */
const CONFIG_FILE = path.join(os.homedir(), '.skillhub', 'config.json');

/** 种子文件向上查找的最大层级 */
const SEED_LOOKUP_MAX_DEPTH = 5;

/** 请求超时（毫秒） */
const TIMEOUT_MS = 3000;

/** 重试间隔（毫秒） */
const RETRY_DELAYS = [1000, 3000, 10000];

/**
 * 是否已通过环境变量关闭上报
 *
 * SKILL.md 中的指令告知用户可用此开关关闭上报，这里是该承诺的实现：
 * 命中时不读取凭据、不发起任何网络请求。
 *
 * @returns {boolean}
 */
function isReportDisabled() {
  const raw = process.env[DISABLE_ENV_NAME];
  if (!raw) {
    return false;
  }
  const value = raw.trim().toLowerCase();
  return value === '1' || value === 'true' || value === 'yes';
}

/**
 * 发送 JSON 请求
 * @param {string} urlPath 接口路径
 * @param {object} body 请求体
 * @param {object} headers 额外请求头
 * @returns {Promise<{status: number, data: object}>}
 */
async function postJson(urlPath, body, headers = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const response = await fetch(`${resolveBaseUrl()}${urlPath}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...headers },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    let data = {};
    try {
      data = await response.json();
    } catch (ignored) {
      // 响应不是JSON，忽略
    }
    return { status: response.status, data };
  } finally {
    clearTimeout(timer);
  }
}

/**
 * 读取文件内容，不存在返回 null
 * @param {string|null} file 文件路径
 * @returns {string|null}
 */
function readFileSafe(file) {
  if (!file) {
    return null;
  }
  try {
    return fs.readFileSync(file, 'utf8').trim();
  } catch (ignored) {
    return null;
  }
}

/**
 * 定位包内种子文件
 *
 * 从本文件所在目录向上逐级查找，兼容三种摆放方式：
 * 脚本放在 skill 根目录、放在 .skillhub/ 目录下、放在任意子目录中。
 *
 * @returns {string|null} 种子文件路径，未找到返回 null
 */
function findSeedFile() {
  let dir = __dirname;
  for (let depth = 0; depth < SEED_LOOKUP_MAX_DEPTH; depth += 1) {
    const nested = path.join(dir, '.skillhub', 'seed');
    if (fs.existsSync(nested)) {
      return nested;
    }
    // 脚本本身就放在 .skillhub 目录下时，种子是同级文件
    if (path.basename(dir) === '.skillhub') {
      const sibling = path.join(dir, 'seed');
      if (fs.existsSync(sibling)) {
        return sibling;
      }
    }
    const parent = path.dirname(dir);
    if (parent === dir) {
      break;
    }
    dir = parent;
  }
  return null;
}

/** 种子解析结果缓存，避免同一次运行内重复读盘 */
let seedInfoCache;

/**
 * 读取并解析包内种子文件
 *
 * @returns {object} 种子信息，读不到或格式错误时返回空对象
 */
function readSeedInfo() {
  if (seedInfoCache !== undefined) {
    return seedInfoCache;
  }
  const raw = readFileSafe(findSeedFile());
  if (!raw) {
    seedInfoCache = {};
    return seedInfoCache;
  }
  try {
    seedInfoCache = JSON.parse(raw) || {};
  } catch (ignored) {
    seedInfoCache = {};
  }
  return seedInfoCache;
}

/**
 * 解析平台地址
 *
 * 优先级：环境变量 > 包内种子携带的地址 > 默认值。
 * 种子里的地址由平台在下载时写入，因此测试环境下载的包会自动上报到测试环境，
 * 使用者不需要配置任何环境变量。
 *
 * @returns {string} 平台地址（不含结尾斜杠）
 */
function resolveBaseUrl() {
  const fromEnv = process.env.SKILLHUB_BASE_URL;
  if (fromEnv && fromEnv.trim()) {
    return fromEnv.trim().replace(/\/+$/, '');
  }
  const fromSeed = readSeedInfo().baseUrl;
  if (fromSeed && String(fromSeed).trim()) {
    return String(fromSeed).trim().replace(/\/+$/, '');
  }
  return DEFAULT_BASE_URL;
}

/**
 * 保存长期凭据到用户 home 目录，权限 600
 * @param {string} credential 长期凭据
 */
function saveCredential(credential) {
  try {
    fs.mkdirSync(path.dirname(CREDENTIAL_FILE), { recursive: true });
    fs.writeFileSync(CREDENTIAL_FILE, credential, { mode: 0o600 });
  } catch (ignored) {
    // 落盘失败不阻断本次上报，下次运行会重新绑定
  }
}

/**
 * 用包内种子换取长期凭据
 * @returns {Promise<string|null>} 长期凭据，失败返回 null
 */
async function bindWithSeed() {
  const seedInfo = readSeedInfo();
  if (!seedInfo.seed) {
    return null;
  }

  const { status, data } = await postJson('/open/skill/usage/bind', {
    seed: seedInfo.seed,
    clientType: process.env.SKILLHUB_CLIENT_TYPE || 'unknown',
    hostName: os.hostname(),
  });
  if (status === 200 && data && data.code === 200 && data.data && data.data.credential) {
    saveCredential(data.data.credential);
    return data.data.credential;
  }
  return null;
}

/**
 * 按优先级获取上报凭据
 * 环境变量 > 本地长期凭据 > 包内种子换取
 * @returns {Promise<string|null>}
 */
async function resolveCredential() {
  if (process.env.SKILLHUB_CREDENTIAL) {
    return process.env.SKILLHUB_CREDENTIAL.trim();
  }
  const local = readFileSafe(CREDENTIAL_FILE);
  if (local) {
    return local;
  }
  return bindWithSeed();
}

/**
 * 读取兜底工号
 * @returns {string|null}
 */
function resolveJobNumber() {
  if (process.env.SKILLHUB_JOB_NUMBER) {
    return process.env.SKILLHUB_JOB_NUMBER.trim();
  }
  const raw = readFileSafe(CONFIG_FILE);
  if (!raw) {
    return null;
  }
  try {
    return JSON.parse(raw).jobNumber || null;
  } catch (ignored) {
    return null;
  }
}

/**
 * 从包内种子文件读取 skillId / skillName，作为调用方未显式传入时的默认值
 * @returns {object}
 */
function readSeedSkillInfo() {
  const info = readSeedInfo();
  return { skillId: info.skillId, skillName: info.skillName };
}

/**
 * 格式化时间为 yyyy-MM-dd HH:mm:ss
 * @param {Date} date 时间
 * @returns {string}
 */
function formatDateTime(date) {
  const pad = (n) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} `
    + `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

/**
 * 睡眠
 * @param {number} ms 毫秒
 * @returns {Promise<void>}
 */
function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * 上报一次 skill 使用情况
 *
 * @param {object} options 上报参数
 * @param {number} [options.skillId] Skill ID，缺省时从包内种子读取
 * @param {string} [options.skillName] Skill 标识名，缺省时从包内种子读取
 * @param {string} [options.skillVersion] 版本号
 * @param {number} [options.quantity] 业务结果数量，如缺陷数
 * @param {string} [options.metricKey] 数量对应的指标标识，传 quantity 时必须一起传
 * @param {string} [options.metricUnit] 数量单位
 * @param {string} [options.remark] 备注
 * @param {string} [options.extJson] 扩展字段（JSON 字符串）
 * @param {string} [options.clientType] 调用端
 * @param {Date}   [options.useTime] 使用时间，默认当前时间
 * @returns {Promise<boolean>} 是否上报成功。失败不抛异常
 */
async function reportUsage(options = {}) {
  try {
    if (isReportDisabled()) {
      return false;
    }
    const seedSkill = readSeedSkillInfo();
    const skillId = options.skillId != null ? options.skillId : seedSkill.skillId;
    const skillName = options.skillName || seedSkill.skillName;
    if (skillId == null || !skillName) {
      return false;
    }

    const payload = {
      skillId,
      skillName,
      useTime: formatDateTime(options.useTime instanceof Date ? options.useTime : new Date()),
      skillVersion: options.skillVersion,
      quantity: options.quantity,
      metricKey: options.metricKey,
      metricUnit: options.metricUnit,
      remark: options.remark,
      extJson: options.extJson,
      clientType: options.clientType || process.env.SKILLHUB_CLIENT_TYPE,
      requestId: crypto.randomUUID(),
    };

    let credential = await resolveCredential();
    if (!credential) {
      // 兜底：无凭据时用工号自报身份
      payload.jobNumber = resolveJobNumber() || undefined;
    }

    for (let attempt = 0; attempt <= RETRY_DELAYS.length; attempt += 1) {
      const headers = credential ? { 'X-Report-Credential': credential } : {};
      const { status, data } = await postJson('/open/skill/usage/report', payload, headers);

      if (status === 200 && data && data.code === 200) {
        return true;
      }
      // 参数问题重试无意义
      if (data && data.code === 400) {
        return false;
      }
      // 凭据失效，尝试重新绑定一次
      if (status === 401 && credential) {
        credential = await bindWithSeed();
        if (!credential) {
          payload.jobNumber = resolveJobNumber() || undefined;
        }
      }
      if (attempt < RETRY_DELAYS.length) {
        await sleep(RETRY_DELAYS[attempt]);
      }
    }
    return false;
  } catch (ignored) {
    // 上报失败绝不影响 skill 主流程
    return false;
  }
}

module.exports = { reportUsage, isReportDisabled };
