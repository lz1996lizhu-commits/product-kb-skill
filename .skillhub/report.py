"""SkillHub 使用情况上报客户端（Python 版）.

直接复制到 skill 目录中使用。封装了：
  - 凭据优先级读取：环境变量 > ~/.skillhub/credential > 包内 .skillhub/seed（首次绑定）
  - 首次运行用种子换取长期凭据并落盘
  - 幂等 requestId 生成
  - 超时与静默重试

铁律：上报失败绝不影响 skill 主流程，所有异常在内部吞掉。
仅依赖标准库，无需安装第三方包。
"""

import json
import os
import socket
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime

# 平台地址兜底值：包内种子没带地址、也没配环境变量时使用
DEFAULT_BASE_URL = "https://skills.kingdee.com"

# 关闭上报的环境变量名，取值 1/true/yes 时不发送任何请求
DISABLE_ENV_NAME = "SKILLHUB_DISABLE_REPORT"

# 长期凭据保存路径，所有 skill 共用
CREDENTIAL_FILE = os.path.join(os.path.expanduser("~"), ".skillhub", "credential")

# 兜底配置（工号）保存路径
CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".skillhub", "config.json")

# 种子文件向上查找的最大层级
SEED_LOOKUP_MAX_DEPTH = 5

# 请求超时（秒）
TIMEOUT_SECONDS = 3

# 重试间隔（秒）
RETRY_DELAYS = (1, 3, 10)

# 种子解析结果缓存，None 表示尚未读取
_SEED_CACHE = None


def is_report_disabled():
    """是否已通过环境变量关闭上报.

    SKILL.md 中的指令告知用户可用此开关关闭上报，这里是该承诺的实现：
    命中时不读取凭据、不发起任何网络请求。

    :return: True 表示本机已关闭上报
    """
    raw = os.environ.get(DISABLE_ENV_NAME)
    if not raw:
        return False
    return raw.strip().lower() in ("1", "true", "yes")


def _post_json(url_path, body, headers=None):
    """发送 JSON 请求.

    :param url_path: 接口路径
    :param body: 请求体字典
    :param headers: 额外请求头
    :return: (状态码, 响应字典)
    """
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        _resolve_base_url() + url_path,
        data=data,
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            payload = response.read().decode("utf-8")
            return response.status, json.loads(payload) if payload else {}
    except urllib.error.HTTPError as error:
        payload = error.read().decode("utf-8", errors="ignore")
        try:
            return error.code, json.loads(payload) if payload else {}
        except ValueError:
            return error.code, {}


def _read_file(path):
    """读取文件内容，不存在返回 None."""
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return None


def _find_seed_file():
    """定位包内种子文件.

    从本文件所在目录向上逐级查找，兼容三种摆放方式：
    脚本放在 skill 根目录、放在 .skillhub/ 目录下、放在任意子目录中。

    :return: 种子文件路径，未找到返回 None
    """
    directory = os.path.dirname(os.path.abspath(__file__))
    for _ in range(SEED_LOOKUP_MAX_DEPTH):
        nested = os.path.join(directory, ".skillhub", "seed")
        if os.path.exists(nested):
            return nested
        # 脚本本身就放在 .skillhub 目录下时，种子是同级文件
        if os.path.basename(directory) == ".skillhub":
            sibling = os.path.join(directory, "seed")
            if os.path.exists(sibling):
                return sibling
        parent = os.path.dirname(directory)
        if parent == directory:
            break
        directory = parent
    return None


def _save_credential(credential):
    """保存长期凭据到用户 home 目录，权限 600."""
    try:
        os.makedirs(os.path.dirname(CREDENTIAL_FILE), exist_ok=True)
        with open(CREDENTIAL_FILE, "w", encoding="utf-8") as handle:
            handle.write(credential)
        os.chmod(CREDENTIAL_FILE, 0o600)
    except OSError:
        # 落盘失败不阻断本次上报，下次运行会重新绑定
        pass


def _read_seed():
    """读取包内种子信息，失败返回空字典.

    结果缓存在模块级变量中，避免同一次运行内重复读盘。
    """
    global _SEED_CACHE
    if _SEED_CACHE is not None:
        return _SEED_CACHE
    raw = _read_file(_find_seed_file())
    if not raw:
        _SEED_CACHE = {}
        return _SEED_CACHE
    try:
        parsed = json.loads(raw)
        _SEED_CACHE = parsed if isinstance(parsed, dict) else {}
    except ValueError:
        _SEED_CACHE = {}
    return _SEED_CACHE


def _resolve_base_url():
    """解析平台地址.

    优先级：环境变量 > 包内种子携带的地址 > 默认值。
    种子里的地址由平台在下载时写入，因此测试环境下载的包会自动上报到测试环境，
    使用者不需要配置任何环境变量。

    :return: 平台地址（不含结尾斜杠）
    """
    from_env = os.environ.get("SKILLHUB_BASE_URL")
    if from_env and from_env.strip():
        return from_env.strip().rstrip("/")
    from_seed = _read_seed().get("baseUrl")
    if from_seed and str(from_seed).strip():
        return str(from_seed).strip().rstrip("/")
    return DEFAULT_BASE_URL


def _bind_with_seed():
    """用包内种子换取长期凭据.

    :return: 长期凭据，失败返回 None
    """
    seed_info = _read_seed()
    seed = seed_info.get("seed")
    if not seed:
        return None

    status, data = _post_json(
        "/open/skill/usage/bind",
        {
            "seed": seed,
            "clientType": os.environ.get("SKILLHUB_CLIENT_TYPE", "unknown"),
            "hostName": socket.gethostname(),
        },
    )
    if status == 200 and data.get("code") == 200:
        credential = (data.get("data") or {}).get("credential")
        if credential:
            _save_credential(credential)
            return credential
    return None


def _resolve_credential():
    """按优先级获取上报凭据：环境变量 > 本地长期凭据 > 包内种子换取."""
    env_credential = os.environ.get("SKILLHUB_CREDENTIAL")
    if env_credential:
        return env_credential.strip()
    local = _read_file(CREDENTIAL_FILE)
    if local:
        return local
    return _bind_with_seed()


def _resolve_job_number():
    """读取兜底工号."""
    env_job_number = os.environ.get("SKILLHUB_JOB_NUMBER")
    if env_job_number:
        return env_job_number.strip()
    raw = _read_file(CONFIG_FILE)
    if not raw:
        return None
    try:
        return json.loads(raw).get("jobNumber")
    except ValueError:
        return None


def report_usage(
    skill_id=None,
    skill_name=None,
    skill_version=None,
    quantity=None,
    metric_key=None,
    metric_unit=None,
    remark=None,
    ext_json=None,
    client_type=None,
    use_time=None,
):
    """上报一次 skill 使用情况.

    :param skill_id: Skill ID，缺省时从包内种子读取
    :param skill_name: Skill 标识名，缺省时从包内种子读取
    :param skill_version: 版本号
    :param quantity: 业务结果数量，如缺陷数
    :param metric_key: 数量对应的指标标识，传 quantity 时必须一起传
    :param metric_unit: 数量单位
    :param remark: 备注
    :param ext_json: 扩展字段（JSON 字符串）
    :param client_type: 调用端
    :param use_time: 使用时间（datetime），默认当前时间
    :return: 是否上报成功，失败不抛异常
    """
    try:
        if is_report_disabled():
            return False
        seed_info = _read_seed()
        resolved_skill_id = skill_id if skill_id is not None else seed_info.get("skillId")
        resolved_skill_name = skill_name or seed_info.get("skillName")
        if resolved_skill_id is None or not resolved_skill_name:
            return False

        moment = use_time if isinstance(use_time, datetime) else datetime.now()
        payload = {
            "skillId": resolved_skill_id,
            "skillName": resolved_skill_name,
            "useTime": moment.strftime("%Y-%m-%d %H:%M:%S"),
            "requestId": str(uuid.uuid4()),
        }
        optional_fields = {
            "skillVersion": skill_version,
            "quantity": quantity,
            "metricKey": metric_key,
            "metricUnit": metric_unit,
            "remark": remark,
            "extJson": ext_json,
            "clientType": client_type or os.environ.get("SKILLHUB_CLIENT_TYPE"),
        }
        payload.update({key: value for key, value in optional_fields.items() if value is not None})

        credential = _resolve_credential()
        if not credential:
            # 兜底：无凭据时用工号自报身份
            job_number = _resolve_job_number()
            if job_number:
                payload["jobNumber"] = job_number

        for attempt in range(len(RETRY_DELAYS) + 1):
            headers = {"X-Report-Credential": credential} if credential else {}
            status, data = _post_json("/open/skill/usage/report", payload, headers)

            if status == 200 and data.get("code") == 200:
                return True
            # 参数问题重试无意义
            if data.get("code") == 400:
                return False
            # 凭据失效，尝试重新绑定一次
            if status == 401 and credential:
                credential = _bind_with_seed()
                if not credential:
                    job_number = _resolve_job_number()
                    if job_number:
                        payload["jobNumber"] = job_number
            if attempt < len(RETRY_DELAYS):
                time.sleep(RETRY_DELAYS[attempt])
        return False
    except Exception:  # noqa: BLE001 - 上报失败绝不影响 skill 主流程
        return False
