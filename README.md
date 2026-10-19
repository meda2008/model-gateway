# 统一模型网关（model-gateway）

[![ci](https://github.com/meda2008/model-gateway/actions/workflows/ci.yml/badge.svg)](https://github.com/meda2008/model-gateway/actions/workflows/ci.yml)
[![license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)

把 **WorkBuddy 云服务** 和 **Qoder** 的免费模型能力，桥接成一个**自带 API Key 的 OpenAI 兼容服务**，供任何客户端（Cherry Studio、ChatBox、Cline、Dify、自研脚本）使用。**单进程、跨平台（Windows / macOS / Linux）、零外部依赖服务**。

```
                ┌──────────────────────────────────────────────┐
                │           统一模型网关（唯一进程）              │
                │  OpenAI 兼容: /v1/chat/completions, /v1/models│
                │  自有 Key 体系: sk-gw-xxx（SQLite + 限流）      │
                │  路由: free/auto 降级链 / wb/<id> / qoder/<id> │
                └───────┬───────────────────────┬──────────────┘
                        │                       │
        ┌───────────────▼─────────────┐   ┌─────▼──────────────────────┐
        │ WorkBuddyBackend            │   │ QoderDirectBackend         │
        │ 云服务 LLM API（免密钥）      │   │ 内嵌直连 api2-v2.qoder.sh  │
        │ POST /.cloud/llm/chat/...   │   │ token 自动刷新，无第二套 key │
        │ 头: x-wb-webapp-access-key  │   │ 登录: python -m gateway     │
        │ 仅流式 → 网关合成非流式       │   │        qoder-login         │
        └─────────────────────────────┘   └────────────────────────────┘
```

## 特性

- **单进程双后端**：WorkBuddy 云服务 LLM + Qoder 直连（内嵌移植，无需第二个进程/端口/密钥）
- **OpenAI 兼容**：`/v1/chat/completions`、`/v1/models`；流式透传，非流式自动合成
- **自有 Key 体系**：`sk-gw-` 前缀，SQLite 存哈希；每 key 独立 RPM/日限；用量对账
- **双池降级**：`free/auto` 按链路自动故障转移（首包前生效，流开始后不重复输出）
- **跨平台**：Windows / macOS / Linux 一致行为；CI 在三个 OS × 三个 Python 版本上验证
- **可复现**：`requirements.lock.txt` 锁定依赖；Docker 一键部署

## 1. 快速开始

### 方式 A：一键脚本（推荐）

```bash
# macOS / Linux
chmod +x run.sh
./run.sh                      # 首次运行自动建 venv + 装依赖 + 起网关
./run.sh qoder-login          # Qoder 设备登录（一次性，浏览器批准）
./run.sh qoder-status         # 查看 Qoder 账号档位与积分

# Windows（命令提示符/PowerShell）
run.bat
run.bat qoder-login
run.bat qoder-status
```

### 方式 B：手动四步

```bash
# ① 建虚拟环境并装依赖（Python 3.10+）
python -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate.bat
pip install -r requirements.txt          # 或 requirements.lock.txt（精确锁定）

# ② Qoder 一次性设备登录（token 存 ~/.qoder2api-auth.json；已登录过可跳过）
python -m gateway qoder-login

# ③ 配置
cp .env.example .env
#    填 WB_ENDPOINT / WB_PUBLISHABLE_KEY（WorkBuddy 云服务，见第 7 节）
#    QODER_MODE=direct 已是默认值，Qoder 侧无需其他配置

# ④ 起网关 + 签发 key
python -m gateway                               # 监听 127.0.0.1:8317
python manage_keys.py add --name my-client      # 打印 sk-gw-xxx，只显示一次
```

### 分 OS 备注

| OS | 注意事项 |
|---|---|
| Windows | 装 Python 时勾选 "Add python.exe to PATH"；`run.bat` 纯 ASCII 无编码问题；中文控制台已内置 UTF-8 重配置 |
| macOS | 用 `python3`（系统自带的是 `python3`）；`run.sh` 首次需 `chmod +x` |
| Linux | 无桌面环境时 `qoder-login` 不会自动开浏览器，手动复制终端里打印的 URL 即可 |

## 2. 配置（.env 全量）

```bash
cp .env.example .env
```

| 变量 | 默认 | 说明 |
|---|---|---|
| `GATEWAY_HOST` | `127.0.0.1` | 监听地址；局域网/服务器访问设 `0.0.0.0` |
| `GATEWAY_PORT` | `8317` | 监听端口 |
| `GATEWAY_DB` | `./gateway.db` | SQLite（key 哈希 + 限流计数 + 用量日志） |
| `DEFAULT_RPM` / `DEFAULT_DAILY_REQUESTS` | `60` / `2000` | 新 key 的默认限额 |
| `REQUEST_TIMEOUT` | `180` | 上游请求超时（秒） |
| `FREE_AUTO_CHAIN` | `workbuddy,qoder` | `free/auto` 的降级链顺序 |
| `NO_PROXY` / `no_proxy` | `127.0.0.1,localhost` | 本机回环不走系统代理（Qoder 直连需要） |
| `WB_ENDPOINT` / `WB_PUBLISHABLE_KEY` | 空 | WorkBuddy 云服务 `publicConfig`（见第 7 节） |
| `WB_ORIGIN` | 取 endpoint origin | Origin 校验不一致时手动指定应用发布域名 |
| `WB_MODELS` | 空 | 静态 WB 模型 id 列表（不填则启动时自动拉取目录） |
| `WB_DEFAULT_MODEL` | 空 | `free/auto` 链上 WB 侧模型 id（如 `auto`） |
| `WB_DEFAULT_SYSTEM` | `You are a helpful assistant...` | 调用方没带 system 消息时注入 |
| `QODER_MODE` | `direct` | `direct`=内嵌直连；`proxy`=独立 Qoder2Api 进程 |
| `QODER_AUTH_PATH` | `~/.qoder2api-auth.json` | Qoder 登录态文件路径（Docker 可指向数据卷） |
| `QODER_EXTRA_MODELS` | 空 | `proxy-id=upstream-id,...` Qoder 上新模型时免代码追加 |
| `QODER_UA` | 桌面端 UA | 可选，覆盖上游 User-Agent |
| `QODER_BASE_URL` / `QODER_API_KEY` | — | 仅 `QODER_MODE=proxy` 时生效 |
| `CATALOG_REFRESH_SECONDS` | `300` | 模型目录后台刷新间隔（上游上新后最长这么久自动生效） |
| `MOCK_BACKEND` / `MOCK_A_FAIL_FIRST` | — | 测试用假后端（见 tests/smoke.py） |

## 3. 三种距离

| 场景 | Base URL | 前提 |
|---|---|---|
| 本机 | `http://127.0.0.1:8317/v1` | 默认即可 |
| 局域网 | `http://<局域网IP>:8317/v1` | `.env` 设 `GATEWAY_HOST=0.0.0.0`；防火墙放行端口 |
| 公网 | `https://<域名>/v1` | 服务器部署 + HTTPS（见下） |

**客户端接入的完整指南（Cherry Studio / ChatBox / NextChat / Cline / Dify / LangChain / n8n / 手机 / 反向接入 WorkBuddy 与 Qoder CLI）见 [CLIENTS.md](CLIENTS.md)。**

## 4. 部署到服务器

**systemd（Linux）**：

```ini
# /etc/systemd/system/model-gateway.service
[Unit]
Description=unified-model-gateway
After=network.target

[Service]
WorkingDirectory=/opt/model-gateway
EnvironmentFile=/opt/model-gateway/.env
ExecStart=/opt/model-gateway/.venv/bin/python -m gateway
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

**Docker**：

```bash
docker build -t model-gateway .
docker run -d -p 8317:8317 --env-file .env \
  -v /srv/model-gateway:/data \
  -v ~/.qoder2api-auth.json:/data/qoder-auth.json \
  model-gateway
```

`/data` 存 `gateway.db`；`qoder-auth.json` 是 Qoder 登录态（先在宿主机跑一次 `python -m gateway qoder-login`，再把文件挂进来；不挂则 Qoder 后端不可用，WorkBuddy 不受影响）。

**HTTPS**：外网访问建议套 Caddy / Nginx / Cloudflare Tunnel。**永远不要无鉴权暴露到公网**——两个上游都是免费额度池，被打爆的只有你。

## 5. 调用速验

```bash
curl http://127.0.0.1:8317/v1/chat/completions \
  -H "Authorization: Bearer sk-gw-xxx" -H "Content-Type: application/json" \
  -d '{"model":"free/auto","messages":[{"role":"user","content":"你好"}]}'
```

```python
from openai import OpenAI
client = OpenAI(base_url="http://127.0.0.1:8317/v1", api_key="sk-gw-xxx")
r = client.chat.completions.create(model="free/auto",
                                   messages=[{"role": "user", "content": "你好"}])
print(r.choices[0].message.content)
```

## 6. 模型命名与路由

| 请求的 model | 行为 |
|---|---|
| `free/auto`（或 `auto`） | 按 `FREE_AUTO_CHAIN` 顺序尝试各后端默认模型，失败自动降级 |
| `wb/<id>` | WorkBuddy 后端，`<id>` 为云服务模型目录 id |
| `qoder/<id>` | Qoder 后端（直连或 proxy，取决于 `QODER_MODE`） |
| 裸 `<id>` | 按各后端目录匹配 |

- `GET /v1/models` 返回聚合目录（`free/auto` + `wb/*` + `qoder/*`）
- 降级只发生在**首包发出前**；流开始后上游中断以错误事件收尾

### 新模型自动发现（两边都会不停上新）

| 上游 | 发现方式 | 新模型多久生效 |
|---|---|---|
| WorkBuddy 云服务 | **全自动**：`/.cloud/llm/models` 实时拉取，后台每 `CATALOG_REFRESH_SECONDS`（默认 5 分钟）刷新一次，`/v1/models` 被调用时也即时刷新 | ≤5 分钟，零配置 |
| Qoder（direct） | 上游**没有公开目录端点**（常见路径均 404；桌面端 dynamic-text 目录里有 cmodel/gfmodel/dfmodel 等内部 id，但直连接点不接受）。网关用实测验证过的静态映射 + 三种扩展方式（见下） | 取决于选哪种扩展 |
| Qoder（proxy） | 走 `QODER_BASE_URL/models`，与 WB 同样周期刷新 | ≤5 分钟 |

**Qoder 上新的三种加法（都不用改代码）**：

```bash
# 1) 环境变量追加映射（推荐）：.env 加一行，重启网关
QODER_EXTRA_MODELS=glm-5.4=gmodel2,new-flash=qwen-flash2

# 2) 直接传上游 id：resolve_model 对未知 id 原样透传
#    POST /v1/chat/completions {"model": "qoder/gmodel2", ...}

# 3) 先探测再接入：确认某个 id 上游收不收（每个 1 token，付费模型耗极少积分）
python -m gateway qoder-probe
```

> 实测记录（2026-10-19）：上游接受 `qwen-flash / qmodel / kmodel / dmodel / mmodel / gmodel`；
> 拒绝 `gfmodel / qfmodel / dfmodel / cmodel`（桌面端内部 key）。`gmodel`（GLM-5.3）就是靠
> qoder-probe 发现并加入映射的新模型。注意 `glm-5.3`/`wb/auto` 这类思考模型在小预算下
> 正文为空（token 全耗在 reasoning），留足 max_tokens。

## 7. WorkBuddy 侧对接详解

1. **激活云服务**：在 WorkBuddy 对话里说"给模型网关激活云服务"（弹官方确认框）。激活后得到 `publicConfig`：
   - `endpoint` —— 形如 `https://<应用发布域名>.workbuddy.link`（数据面基址）
   - `publishableKey` —— `wbpk_` 开头（设计上就是前端可见的标识，**不是秘密**，真正的边界是 Origin 校验）
2. **填 .env**：`WB_ENDPOINT=<endpoint>`，`WB_PUBLISHABLE_KEY=<wbpk_...>`
3. **Origin**：服务端校验 Origin 与应用发布域名一致。网关默认取 endpoint 的 origin；若报 `auth_` 类错误，设 `WB_ORIGIN`
4. **选默认模型**：起网关后 `GET /v1/models` 看 WB 侧目录，把选中的 id 填 `WB_DEFAULT_MODEL`
5. **配额**：按应用有创建者配额（免用户 key，超额报 `quota_` 错误）；用量到 WorkBuddy 管理面板看

> 开发时激活的应用目录含 32 个模型：`auto` / `step-5-preview` / `hy4-preview` / `glm-5.3`(-flash) /
> `kimi-k3-1` / `deepseek-v4-pro`(-flash) / `minimax-m3` / `hunyuan-2.0-thinking` 等。
> 实测多个模型 `credit: 0` 零积分调用；思考类模型在小 `max_tokens`（≤200）时正文可能为空，留足预算。

## 8. Qoder 侧对接详解（两种模式）

**direct（默认）**——登录态管理与上游调用内嵌于网关（`gateway/qoder_direct.py`，移植自社区 Qoder2Api，MIT）：

- 上游 `https://api2-v2.qoder.sh/model/v1/chat/completions`（OpenAI 格式，Bearer token）
- 登录态 `~/.qoder2api-auth.json`（token + refreshToken，过期自动刷新）；`QODER_AUTH_PATH` 可改路径
- 登录/查状态：`python -m gateway qoder-login` / `qoder-status`
- 模型池（2026-10-19 实测验证）：`qwen3.8-flash`（免费零积分）/ `qwen3.5-flash` / `qwen3.7-plus` /
  `glm-5.3` / `kimi-k2.7-code` / `deepseek-v4-pro` / `minimax-m3`(=`minimax-m2.5`)。
  上游无公开目录端点，上新后用 `python -m gateway qoder-probe` 探测 + `QODER_EXTRA_MODELS` 追加（见 §6）

**proxy（备选）**——独立 Qoder2Api 进程：

```bash
git clone https://github.com/ErfanBagheri404/Qoder2Api.git && cd Qoder2Api
pip install cryptography
python main.py --login
python main.py --no-menu --port 61025 --api-key sk-qoder-local
```

`.env` 设 `QODER_MODE=proxy` + `QODER_BASE_URL=http://127.0.0.1:61025/v1` + `QODER_API_KEY=sk-qoder-local`。
**proxy 模式下 `--api-key` 必须与 `QODER_API_KEY` 完全一致**，否则网关侧全部 401。

> `QODER_MODE=proxy` 时 `QODER_BASE_URL` 也可以是任何 OpenAI 兼容服务（自建 Ollama、其他中转）。
> 已知限制：tool 角色消息会被折叠成 user；reasoning_content 透传。

## 9. 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| `python: command not found` | 不在 PATH | Windows 重装勾选 Add to PATH；macOS/Linux 用 `python3` |
| 中文乱码/打印崩溃 | Windows GBK 代码页 | 已内置 UTF-8 重配置；老旧终端可 `chcp 65001` |
| 401 invalid api key | key 错/被吊销/没建 | `python manage_keys.py list` 核对 |
| 429 + Retry-After | 超 RPM/日限 | 等待，或重建 key 调大 `--rpm/--daily` |
| 400 model_not_found | 模型名不在目录 | `GET /v1/models` 核对 |
| 502 `workbuddy upstream HTTP 401/403` | Origin 不匹配 | 设 `WB_ORIGIN` 为应用发布域名 |
| 502 `qoder not logged in` | 直连未登录/失效 | `python -m gateway qoder-login`；Docker 检查挂载 |
| 502 `upstream connection failed` | 上游不可达 | 直连查网络/代理；proxy 模式确认 Qoder2Api 在跑；`.env` 已含 `NO_PROXY` |
| 局域网连不上 | 绑定了 127.0.0.1 / 防火墙 | `GATEWAY_HOST=0.0.0.0` + 放行端口 |
| 流式中途错误事件 | 上游断流 | 重试；WB 侧仅流式，断流无恢复 |

## 10. 目录结构与常用命令

```
model-gateway/
├── run.sh / run.bat          # 一键启动（自动建 venv + 装依赖）
├── gateway/
│   ├── __main__.py     # 入口：python -m gateway [qoder-login|qoder-status]
│   ├── config.py       # 环境变量配置 + .env 加载
│   ├── keystore.py     # sk-gw- key 签发/校验/限流/用量对账（SQLite）
│   ├── sse.py          # SSE 解析（各后端共用）
│   ├── qoder_direct.py # Qoder 直连客户端（内嵌，移植自 Qoder2Api）
│   ├── backends.py     # WorkBuddy / Qoder 直连 / OpenAI 兼容(proxy) / Mock 适配器
│   └── app.py          # FastAPI：/v1/chat/completions、/v1/models、/healthz
├── manage_keys.py      # key 管理 CLI：add / list / revoke / usage
├── tests/smoke.py      # 端到端测试（mock 后端，13 项断言）
├── tests/live_failover.py  # 真实双池降级测试（两个方向）
├── CLIENTS.md          # 别处调用指南（各客户端配置）
├── .env.example
├── Dockerfile / .dockerignore
├── pyproject.toml      # pip install . 后得到 model-gateway 命令
├── requirements.txt / requirements.lock.txt
└── LICENSE
```

```bash
python -m gateway                          # 启动网关（唯一进程）
python -m gateway qoder-login              # Qoder 设备登录
python -m gateway qoder-status             # Qoder 账号/积分状态
python -m gateway qoder-probe              # 探测 Qoder 上游可用模型 id（上新后用）
python manage_keys.py add --name <用途> [--rpm 60] [--daily 2000]
python manage_keys.py list
python manage_keys.py revoke --name <用途>
python manage_keys.py usage [--days 7]     # 按 key/后端/模型对账 token 与耗时
python tests/smoke.py                      # 回归测试（13 项）
python tests/live_failover.py              # 真实双池降级（2 项，需 Qoder 已登录）
```

## 11. 已知边界

- `n>1`（多候选）仅非流式部分支持；WB 侧合成只取 choices[0]
- tools 调用：WB 侧透传 delta；Qoder 上游把 tool 角色折叠成 user，工具链不完整——依赖工具调用优先用 `wb/*` 模型
- 单实例部署（内存态目录缓存 + SQLite 计数）；多实例需换 Redis 计数
- 限流计数在请求进入时即消耗（含最终失败的请求），防打爆优先
- 内嵌的 Qoder 直连客户端固定自 Qoder2Api 当前版本；上游 API 变动时以 Qoder2Api 仓库为准同步 `gateway/qoder_direct.py`

## 12. License

MIT，见 [LICENSE](LICENSE)。`gateway/qoder_direct.py` 移植自 Qoder2Api（MIT License, Copyright (c) 2026 ErfanBagheri404），版权声明保留在文件头。
