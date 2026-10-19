# 别处调用指南：三要素 + 各客户端配置

网关是标准 OpenAI 兼容服务。任何支持"自定义 OpenAI 端点"的客户端，填**三要素**即可用，无需改代码。

```
Base URL : http://127.0.0.1:8317/v1        ← 注意带 /v1；别处访问时换成对应 IP/域名
API Key  : sk-gw-xxxxxxxx                  ← manage_keys.py add 签发，仅显示一次
Model    : free/auto                       ← 或 wb/step-5-preview、qoder/qwen3.8-flash 等 39 个
```

> 启动网关：`./run.sh`（macOS/Linux）或 `run.bat`（Windows），首次自动建 venv 装依赖；也可 `python -m gateway`。
> 服务不在本机时：把 Base URL 的 `127.0.0.1` 换成网关机器的 IP/域名，并确认网关以 `GATEWAY_HOST=0.0.0.0` 启动（见下文"三种距离"）。

## 0. 30 秒自测（curl）

```bash
curl http://127.0.0.1:8317/v1/chat/completions \
  -H "Authorization: Bearer sk-gw-xxxxxxxx" -H "Content-Type: application/json" \
  -d '{"model":"free/auto","messages":[{"role":"user","content":"你好"}]}'
```

返回标准 `chat.completion` JSON 即通。

## 1. 三种距离与对应配置

| 场景 | Base URL | 前提 |
|---|---|---|
| 本机（网关跑在这台电脑） | `http://127.0.0.1:8317/v1` | 默认即可 |
| 局域网（手机/别的电脑） | `http://<局域网IP>:8317/v1`，如 `http://192.168.1.5:8317/v1` | `.env` 设 `GATEWAY_HOST=0.0.0.0`；Windows 防火墙放行 8317 |
| 公网（随时随地） | `https://<你的域名>/v1` | 按 README §4 部署到服务器 + Caddy/Nginx/Tunnel 上 HTTPS |

查本机局域网 IP：Windows `ipconfig` 看 "IPv4 地址"；macOS `ipconfig getifaddr en0`；Linux `ip addr`。

## 2. 代码调用（零改造）

Python（openai SDK，已实测）：

```python
from openai import OpenAI
client = OpenAI(base_url="http://127.0.0.1:8317/v1", api_key="sk-gw-xxxxxxxx")

# 模型发现
[m.id for m in client.models.list()]          # 39 个：free/auto + wb/* + qoder/*

# 对话（非流式）
r = client.chat.completions.create(model="free/auto",
                                   messages=[{"role": "user", "content": "你好"}])
print(r.choices[0].message.content)

# 对话（流式）
for chunk in client.chat.completions.create(model="qoder/qwen3.8-flash", stream=True,
                                            messages=[{"role": "user", "content": "你好"}]):
    print(chunk.choices[0].delta.content or "", end="")
```

也支持环境变量式（适配所有读 `OPENAI_*` 的工具）：

```bash
export OPENAI_BASE_URL=http://127.0.0.1:8317/v1
export OPENAI_API_KEY=sk-gw-xxxxxxxx
```

LangChain：

```python
from langchain_openai import ChatOpenAI
llm = ChatOpenAI(base_url="http://127.0.0.1:8317/v1", api_key="sk-gw-xxxxxxxx",
                 model="free/auto")
```

Node（openai 包同理）：`new OpenAI({ baseURL: "http://127.0.0.1:8317/v1", apiKey: "sk-gw-xxx" })`

## 3. 图形客户端配置速查

| 客户端 | 配置路径（设置 → 添加模型/供应商） | 填法 |
|---|---|---|
| **Cherry Studio** | 设置 → 模型服务 → 添加 → 选 "OpenAI API" | API 地址 `http://127.0.0.1:8317/v1`；密钥 `sk-gw-xxx`；添加模型名 `free/auto` |
| **ChatBox** | 设置 → 模型 → 自定义 API 提供方 | 选 OpenAI 兼容；API Host、Key、模型名同上 |
| **NextChat** | 设置 → 自定义端点 | BaseURL `http://127.0.0.1:8317/v1` + Key；模型下拉里搜 `free/auto` |
| **Cline / Roo**（VS Code） | API Provider 选 "OpenAI Compatible" | Base URL + Key + Model ID `free/auto` |
| **Dify** | 模型供应商 → "OpenAI-API-compatible" | API Base URL + Key；模型名 `free/auto` 等 |
| **n8n** | Credentials → OpenAI | 把 Base URL 改成网关地址（部分版本叫 "Base URL" 覆盖项） |
| **LobeChat** | 设置 → 语言模型 → 自定义服务商 | OpenAI 兼容，填三要素 |
| **open-webui** | 管理员设置 → 连接 → OpenAI API | 填 Base URL + Key，模型自动出现在列表 |

> 图形客户端的菜单名随版本可能略有差异，认准两点即可：① 供应商类型选 **OpenAI 兼容/自定义**；② 地址**带 `/v1`**。

## 4. 反向接入：把网关喂回给 WorkBuddy / Qoder CLI

这两个工具支持"自定义模型"（OpenAI 兼容），等于用一个 `sk-gw-` key 把两个免费池变成它们的模型：

- **WorkBuddy 桌面端**：设置 → 模型 → 自定义模型 → 添加 → 自定义 API
  - 接口地址 `http://127.0.0.1:8317/v1/chat/completions`
  - API Key `sk-gw-xxx`，模型名称 `free/auto`
- **Qoder CLI**（v1.1.50+）：`/model` → Custom → Add custom model → OpenAI-compatible
  - Base URL `http://127.0.0.1:8317/v1`，Model ID `free/auto`，API Key `sk-gw-xxx`

## 5. 模型怎么选

| 场景 | 模型 |
|---|---|
| 日常/不确定 | `free/auto`（WB 优先，挂了自动降级 Qoder） |
| 大批量/零积分 | `qoder/qwen3.8-flash`（Qoder 免费模型，不烧双方积分） |
| 要质量 | `wb/step-5-preview`、`wb/glm-5.3`、`wb/kimi-k3-1`、`wb/deepseek-v4-pro`、`qoder/glm-5.3` |
| 完整列表 | `GET /v1/models` 或客户端里的模型列表（43 个，两边上新后 5 分钟内自动进目录） |

注意：`free/auto`（WB 侧）、`qoder/glm-5.3` 等可能路由到思考模型，小 `max_tokens`（≤600）时正文可能为空、token 全在 reasoning——留足预算（1000+）。Qoder 侧新模型可用 `qoder-probe` 探测后用 `QODER_EXTRA_MODELS` 追加（见 README §6）。

## 6. Key 管理：一个用途一把 key

```bash
python manage_keys.py add --name cline --rpm 60 --daily 2000   # VS Code 里用
python manage_keys.py add --name phone --rpm 20 --daily 500    # 手机上用
python manage_keys.py list                                     # 查看
python manage_keys.py revoke --name phone                      # 吊销（立即生效）
python manage_keys.py usage --days 7                           # 对账：谁用了多少
```

一用途一 key 的好处：限流互不影响、用量可归因、可疑时单独吊销不波及其他。

## 7. 注意事项

- **Base URL 必须带 `/v1`**（个别客户端填"API 域名"时不带，看客户端实际请求路径）
- **http vs https**：本机/局域网用 http 即可；公网务必上 HTTPS，且**不要无鉴权暴露**（两个上游都是免费额度池）
- **429** = 超出该 key 的 RPM/日限，等一会或重建 key 时调大 `--rpm/--daily`
- **400 model_not_found** = 模型名打错，先 `GET /v1/models` 核对
- **思考类模型**（`wb/auto` 可能路由到）在小 `max_tokens`（≤200）时正文可能为空，留足预算
- **tools/function calling**：WB 侧透传；Qoder 侧会把 tool 角色折叠成 user，工具链不完整——依赖工具调用的场景优先用 `wb/*` 模型
- **图片输入**：messages 里的 `image_url` 内容段原样透传，WB 侧多模态模型（如 `wb/glm-5v-turbo`）可用
