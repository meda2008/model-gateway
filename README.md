# 统一模型网关（model-gateway）

把 **WorkBuddy 云服务** 和 **Qoder** 里的免费模型能力，桥接成一个**自带 API Key 的 OpenAI 兼容服务**，供任何客户端（Cherry Studio、ChatBox、9router、自研脚本、其他 Agent）使用。**单进程运行**：Qoder 的登录态管理与上游调用已内嵌（移植自社区 Qoder2Api），不再需要第二个进程/端口/密钥。

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

## 0. 两边免费模型的真相（先读这个）

| | WorkBuddy | Qoder |
|---|---|---|
| 免费模型 | 云服务 LLM API 按应用提供创建者配额（免用户 key） | qwen3.8-flash 零积分；qwen3.7-plus / kimi-k2.7-code / deepseek-v4-pro / minimax-m2.5 花试用积分 |
| 官方出站 API | **没有**。桌面端自带的模型（Step/GLM/Hy3 等）不对外提供 API；官方唯一可编程路径是云服务 LLM API | **没有**。官方只开放了"自定义模型入站"（CLI v1.1.50+ 支持 OpenAI/Anthropic 兼容接入），出站需社区方案 |
| 桥接方式 | 本网关 WorkBuddyBackend（协议提取自官方 SDK） | 内嵌 QoderDirectBackend（移植自社区 Qoder2Api，设备登录，非官方） |
| 关键约束 | 仅流式；服务端校验 Origin 与应用发布域名一致；按应用环境隔离 | 非官方，随时可能失效；有每日调用上限；tool 角色会被折叠成 user |
| 风险 | 免费配额非 SLA，超额返回 quota_ 错误 | 逆向方案有 ToS 风险，仅建议个人自用、作降级后备 |

## 1. 快速开始（4 步）

```bash
# ① 装依赖（本项目开发时已装在 WorkBuddy 托管 venv；自用请建自己的 venv）
cd model-gateway
python -m venv .venv && .venv/bin/pip install -r requirements.txt   # Windows: .venv\Scripts\pip

# ② Qoder 一次性设备登录（已登录过可跳过；token 存 ~/.qoder2api-auth.json）
python -m gateway qoder-login        # 浏览器打开弹出的链接并批准
python -m gateway qoder-status       # 可选：看账号档位与积分余额

# ③ 填 .env（复制 .env.example）
cp .env.example .env
#   QODER_MODE=direct（默认，内嵌直连；proxy=独立 Qoder2Api 进程，备选）
#   WB_ENDPOINT / WB_PUBLISHABLE_KEY 见第 3 节（激活云服务后拿）

# ④ 起网关 + 签发 key
python -m gateway                                        # 监听 127.0.0.1:8317，唯一进程
python manage_keys.py add --name cherry-studio           # 打印 sk-gw-xxx，只显示一次

# ⑤ 任意 OpenAI 客户端连上来
#   Base URL: http://127.0.0.1:8317/v1
#   API Key:  sk-gw-xxx
#   模型:     free/auto（或 qoder/qwen3.8-flash、wb/<模型id>）
```

## 2. 模型命名与路由

| 请求的 model | 行为 |
|---|---|
| `free/auto`（或 `auto`） | 按 `FREE_AUTO_CHAIN` 顺序尝试各后端默认模型，失败自动降级 |
| `wb/<id>` | 指定 WorkBuddy 后端，`<id>` 为云服务模型目录里的 id |
| `qoder/<id>` | 指定 Qoder 后端（直连或 proxy，取决于 `QODER_MODE`） |
| 裸 `<id>` | 按各后端目录匹配（WB 目录启动时自动拉取，也可用 `WB_MODELS` 静态指定） |

- `GET /v1/models` 返回聚合目录：`free/auto` + `wb/*` + `qoder/*`
- 流式请求（`stream: true`）透传 SSE；非流式请求对 WorkBuddy 会自动收集 SSE 合成标准 JSON
- 降级只发生在**首包发出前**；流开始后上游中断会以错误事件收尾（已在响应中说明）

## 3. WorkBuddy 侧对接详解

1. **激活云服务**：在 WorkBuddy 对话里说"给模型网关激活云服务"（会弹官方确认框）。激活后得到 `publicConfig`：
   - `endpoint` —— 形如 `https://<应用发布域名>.workbuddy.link`（数据面基址）
   - `publishableKey` —— `wbpk_` 开头（本来就是设计为前端可见的标识，**不是秘密**，真正的边界是 Origin 校验）
2. **填 .env**：`WB_ENDPOINT=<endpoint>`，`WB_PUBLISHABLE_KEY=<wbpk_...>`
3. **Origin**：服务端校验 Origin 与应用发布域名一致。网关默认取 endpoint 的 origin；若报 `auth_` 类错误，设 `WB_ORIGIN=https://<应用发布域名>`
4. **选默认模型**：起网关后 `GET /v1/models` 看 WB 侧目录，把选中的 id 填 `WB_DEFAULT_MODEL`（不填则 `free/auto` 不含 WB）
5. **配额**：云服务 LLM 按应用有创建者配额（免用户 key，超额报 `quota_` 错误）。用量/配额 SDK 不暴露，到 WorkBuddy 管理面板看

> 本仓库已激活的应用：`wbapp_nn8LMsBtR5Sdx4OJd8hRes`（域名 `model-gateway-89195.app.workbuddy.host`），
> 目录 32 个模型：`auto` / `step-5-preview` / `hy4-preview` / `glm-5.3`(-flash) / `glm-5v-turbo` /
> `kimi-k3-1` / `kimi-k2-thinking` / `deepseek-v4-pro`(-flash) / `minimax-m3` / `hunyuan-2.0-thinking` 等。
> 实测 `step-5-preview` 与 `auto` 均有 `credit: 0` 的零积分调用；思考类模型吃满小 max_tokens 时正文可能为空（reasoning_content 单独存放），调用时留足预算。

## 4. Qoder 侧对接详解（两种模式）

**direct（默认，推荐）**——Qoder2Api 的登录态管理与上游调用已内嵌进网关（`gateway/qoder_direct.py`，移植自社区项目 Qoder2Api）：

- 上游：`https://api2-v2.qoder.sh/model/v1/chat/completions`（OpenAI 格式，Bearer token）
- 登录态存 `~/.qoder2api-auth.json`（token + refreshToken，过期自动刷新）；与独立 Qoder2Api 共用同一文件，两边可随时切换
- 登录/查状态：`python -m gateway qoder-login` / `python -m gateway qoder-status`
- 无需第二个端口、第二套 key（`sk-gw-` 就是唯一钥匙）

**proxy（备选）**——跑独立 Qoder2Api 进程，网关走 OpenAI 兼容适配器：

```bash
git clone https://github.com/ErfanBagheri404/Qoder2Api.git && cd Qoder2Api
pip install cryptography
python main.py --login                                            # 浏览器批准设备登录
python main.py --no-menu --port 61025 --api-key sk-qoder-local    # 常驻代理
```

`.env` 设 `QODER_MODE=proxy` + `QODER_BASE_URL=http://127.0.0.1:61025/v1` + `QODER_API_KEY=sk-qoder-local`。
**注意**：proxy 模式下 `--api-key` 必须与 `QODER_API_KEY` 完全一致，否则网关侧全部 401。

> 两种模式共用同一份模型池：`qwen3.8-flash`（免费零积分）/ `qwen3.7-plus` / `qwen3.5-flash` /
> `kimi-k2.7-code` / `deepseek-v4-pro` / `minimax-m2.5`（后五个花试用积分）。
> 本机账号 `personal_standard`（Free 档），积分用量 0.16%。
> 已知限制：tool 角色消息会被折叠成 user；reasoning_content 透传。

> 另一个备选：`QODER_MODE=proxy` 时把 `QODER_BASE_URL` 指向任何 OpenAI 兼容服务（比如你自建的 Ollama、其他中转），适配器是通用的。

## 5. 部署

**本机**（默认）：`GATEWAY_HOST=127.0.0.1 GATEWAY_PORT=8317`，一条 `python -m gateway` 起全部
**局域网**：`GATEWAY_HOST=0.0.0.0`，key 鉴权强制开启，注意 Windows 防火墙放行端口
**EC2 / 服务器**（systemd）：

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

Docker：`docker build -t model-gateway . && docker run -d -p 8317:8317 --env-file .env model-gateway`
（token 文件在容器外 `~/.qoder2api-auth.json`，需挂载或重新 `qoder-login`）

外网访问建议套一层 Caddy / Nginx / Cloudflare Tunnel 上 HTTPS。**永远不要无鉴权暴露到公网**——两个上游都是免费额度池，被打爆的只有你。

## 6. 调用示例

**完整客户端接入指南（Cherry Studio / ChatBox / NextChat / Cline / Dify / LangChain / n8n / 手机与局域网 / 反向接入 WorkBuddy 与 Qoder CLI）见 [CLIENTS.md](CLIENTS.md)。**

三要素：Base URL `http://127.0.0.1:8317/v1` + API Key `sk-gw-xxx` + 模型 `free/auto`。

curl（流式）：

```bash
curl http://127.0.0.1:8317/v1/chat/completions \
  -H "Authorization: Bearer sk-gw-xxx" -H "Content-Type: application/json" \
  -d '{"model":"free/auto","stream":true,"messages":[{"role":"user","content":"你好"}]}'
```

Python（openai SDK 零改造）：

```python
from openai import OpenAI
client = OpenAI(base_url="http://127.0.0.1:8317/v1", api_key="sk-gw-xxx")
r = client.chat.completions.create(model="free/auto",
                                   messages=[{"role": "user", "content": "你好"}])
print(r.choices[0].message.content)
```

**反向彩蛋**：WorkBuddy（设置 → 模型 → 自定义 API）和 Qoder CLI（v1.1.50+ `/model → Custom`）都支持接入 OpenAI 兼容自定义模型——把这个网关填进去，就等于用一个 `sk-gw-` key 把两个免费池喂回给这两个工具。

## 7. 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| 401 invalid api key | key 错/被吊销/没建 | `python manage_keys.py list` 核对 |
| 429 + Retry-After | 超 RPM 或日限 | 等一会儿，或 `manage_keys.py` 重建时调大 `--rpm/--daily` |
| 400 model_not_found | 模型名不在目录 | `GET /v1/models` 看可用列表 |
| 502，日志含 `workbuddy upstream HTTP 401/403` | Origin 不匹配 | 设 `WB_ORIGIN` 为应用发布域名 |
| 502，日志含 `quota_` | WB 配额超了 | 降级到 Qoder（调整 `FREE_AUTO_CHAIN` 顺序）或升级配额 |
| 502，日志含 `qoder not logged in` | 直连模式未登录/登录态失效 | `python -m gateway qoder-login` 重登 |
| 502，日志含 `upstream connection failed` | 上游不可达 | 直连模式查网络/代理；proxy 模式确认 Qoder2Api 在跑；系统有代理时 `.env` 已含 `NO_PROXY=127.0.0.1,localhost` |
| 流式中途出错误事件 | 上游断流 | 重试；WorkBuddy 侧仅流式，断流无恢复 |

## 8. 目录结构与常用命令

```
model-gateway/
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
├── .env.example
├── Dockerfile
└── requirements.txt
```

```bash
python -m gateway                          # 启动网关（唯一进程）
python -m gateway qoder-login              # Qoder 设备登录
python -m gateway qoder-status             # Qoder 账号/积分状态
python manage_keys.py add --name <用途> [--rpm 60] [--daily 2000]
python manage_keys.py list
python manage_keys.py revoke --name <用途>
python manage_keys.py usage [--days 7]     # 按 key/后端/模型对账 token 与耗时
python tests/smoke.py                      # 回归测试（13 项）
python tests/live_failover.py              # 真实双池降级（2 项）
```

## 9. 已知边界

- `n>1`（多候选）仅非流式部分支持；WB 侧合成只取 choices[0]
- tools 调用：WB 侧透传 delta；Qoder 上游会把 tool 角色折叠成 user，工具链在 Qoder 侧不完整
- 单实例部署（内存态目录缓存 + SQLite 计数）；多实例需换 Redis 计数
- 限流计数在请求进入时即消耗（含最终失败的请求），防打爆优先
- 内嵌的 Qoder 直连客户端固定自 Qoder2Api 当前版本；上游 API 若变动，以 Qoder2Api 仓库更新为准同步 `gateway/qoder_direct.py`

## 10. License

MIT，见 [LICENSE](LICENSE)。`gateway/qoder_direct.py` 移植自 Qoder2Api（MIT License, Copyright (c) 2026 ErfanBagheri404），版权声明保留在文件头。
