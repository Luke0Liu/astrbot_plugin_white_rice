# 白饭账本 · astrbot_plugin_white_rice

把机器人的 **token 消耗**用人设里的说法「**白饭**」汇报出来：既能用自然语言问，也能用指令查。

> 「白饭」是溟月人设里的 token 隐喻（主食白饭／Token 饼干）。
> 所以「**你今天吃了多少大白饭**」＝ 今天消耗了多少 token。

---

## 功能

- **LLM 函数工具** `white_rice_report`：问到「吃了多少白饭／用了多少 token／花了多少钱」时，由模型**自动调用**
- **指令** `/白饭 [天数]`：不依赖模型判断，直接查
- 输出内容：总量 ＋ 按模型拆分（输入／缓存命中／输出／调用次数）＋ 折算金额

## 依赖

| 依赖 | 必要性 | 说明 |
|---|---|---|
| `astrbot_plugin_cost_control` | **可选** | 金额数据来自它；未安装时仍能报 token，只是没有金额 |
| Python 第三方库 | **无** | 仅用标准库 `sqlite3`，不需要 pip 安装任何东西 |

需要能**只读**访问两个 SQLite 文件（路径相对 AstrBot 根目录）：

| 文件 | 表 | 用途 |
|---|---|---|
| `data/data_v4.db` | `provider_stats` | token 用量（权威、完整） |
| `data/plugin_data/astrbot_plugin_cost_control/supplement.db` | `cost_supplements` | 每条请求的折算金额 |

> 两个库都以 `file:...?mode=ro` 只读方式打开，不会写入或损坏它们。

## 用法

| 方式 | 示例 |
|---|---|
| 自然语言 | 「你今天吃了多少大白饭」／「这周用了多少 token」／「花了多少钱」 |
| 指令 | `/白饭`（今天）、`/白饭 7`（近 7 天）、`/白饭 30`（近 30 天） |

参数 `days`：统计**最近 N 天**，默认 1（今天），范围 1–365。

## 输出示例

```
今天一共吃掉 616,170 白饭（token）。
- deepseek-flash：616,170 白饭（输入 129,891 ／ 缓存命中 420,736 ／ 输出 1,491），25 次调用
折算下来大约 0.0032 元（按成本插件已配单价，共 N 条计价记录）。
```

未配单价时最后一行会变成：

```
金额还折算不出来——成本插件里没有为该模型配单价。
```

## 实现要点（踩过的坑）

### 1. 时间口径：库里是 UTC，而「今天」是北京时间

`provider_stats.created_at` 存的是 **UTC**。若直接用 `datetime.now()`（容器 UTC+8）去比较，起点会变成 UTC 的「明天」，**一条都查不到**，于是误报「今天一口白饭都没吃到」。

本插件的做法：按**北京时间自然日**算出起点，再换算成 UTC 查询。
（北京今天 00:00 ＝ UTC 前一天 16:00；实测日界从错误的 2,900,561 修正为正确的 498,629。）

### 2. 必须「每次提问都重新调用」

用量一直在增长，模型却容易在**第二次提问时跳过工具**、直接复述上一次的数字（实测复现：第 1 次调用工具，第 2 次不调用并回答「问两遍也是这个数啦」）。

因此工具描述里写死了约束：

- 每被问一次就重新调用一次；
- 不许沿用对话历史或长期记忆里的旧数字；
- 历史上出现过的「**零／没吃到**」是早期尚无本工具时的错误回答，**一律不可采信**。

> 触发自然语言的**关键不是关键词匹配，而是工具描述**——模型完全依据描述判断何时调用。

### 3. 兜底不撒谎

窗口内确实没有记录、但历史有数据时，会如实说明「这段时间没有记录，历史累计 X 共 N 次调用」，而不是笼统回一句「没吃到」。

## 金额是怎么来的

金额**不是本插件自己算的**，而是把 `cost_control` 记录的每条请求成本求和。要让金额出现，需要在 `cost_control` 里配好单价：

```
POST /api/plug/astrbot_plugin_cost_control/actions/save_config   # 保存即热生效，无需重启
```

> 注意：**不要直接手改 `cost_control` 的 `config.json`**——它会在加载时按自身校验/规范化回写覆盖。
> 本部署已按 DeepSeek 官方价配置（CNY / 百万 token；基准＝空闲价，工作日 09:00-12:00 与 14:00-18:00 高峰 ×2）。

## 已知限制

- **金额依赖 cost_control 的单价配置**，未配则为空；本插件不内置任何价格表
- 金额是**估算**：`cost_control` 的时段策略只支持「星期＋时间」，**表达不了中国法定节假日**（每年约 11 天会略微高估）
- token 仅统计 AstrBot 原生 `provider_stats`，**不含**其他工具的独立计费（如搜索 API 按次计费）
- 当前返回的是**整个机器人**的消耗，**不支持**按群／按用户拆分
- 跨模型汇总时按 `provider_model` 分组；若同一模型被多个 Provider 复用会合并显示

## 文件结构

```
astrbot_plugin_white_rice/
├── main.py          # 工具 + 指令实现
├── metadata.yaml    # 插件元信息
└── README.md        # 本文档
```

## 开发与调试

- 工具注册：`@llm_tool(name="white_rice_report")`，参数与说明写在 **docstring 的 `Args:` 段**里
- 指令注册：`@filter.command("白饭")`，用 `event.plain_result()` 返回文本
- 改完 `main.py` 需**重启 AstrBot** 才生效（插件配置可热更，但代码不行）
- 日志里搜 `white_rice_report` 可看到工具注册；搜 `Tool \`white_rice_report\` Result` 可看到每次的真实返回值
- 手工验证数据源（在容器内执行）：

```python
import sqlite3
con = sqlite3.connect('file:/AstrBot/data/data_v4.db?mode=ro', uri=True)
q = """SELECT provider_model, SUM(token_input_other), SUM(token_input_cached), SUM(token_output)
       FROM provider_stats WHERE datetime(created_at) >= datetime(?)
       GROUP BY 1"""
for row in con.execute(q, ('2026-10-27 16:00:00',)):
    print(row)
```

## 版本

- **1.1.0** —— 修正 UTC／北京时间口径；工具描述加入「必须每次调用」约束（修复第二次提问跳过工具、复述旧值的问题）
- **1.0.0** —— 首个可用版本：LLM 工具 + `/白饭` 指令，读取 `provider_stats` 与 `cost_supplements`