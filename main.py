"""白饭账本 —— 用自然语言查询机器人消耗的 token（人设里叫「白饭」）与折算费用。

数据来源：
  * token 用量：AstrBot 原生库 data_v4.db 的 provider_stats 表（完整、权威）
  * 折算费用：cost_control 插件补充库 supplement.db 的 cost_supplements 表
               （仅当该插件为该模型配置了单价时才有金额）

时间口径（重要）：
  provider_stats.created_at 存的是 **UTC**；而用户说的「今天」是**北京时间**。
  因此按北京时间自然日算出起止，再换算成 UTC 去比较，否则会一条都查不到。
"""

import sqlite3
from datetime import datetime, timedelta, timezone

from astrbot.api import llm_tool, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

NATIVE_DB = "/AstrBot/data/data_v4.db"
COST_DB = "/AstrBot/data/plugin_data/astrbot_plugin_cost_control/supplement.db"

BEIJING = timezone(timedelta(hours=8))
UTC = timezone.utc


def _fmt(n) -> str:
    try:
        return format(int(n or 0), ",")
    except Exception:
        return str(n or 0)


def _window_start_utc(days: int) -> str:
    """按北京时间「最近 days 天」的起点，换算为 UTC 字符串。"""
    days = max(1, min(365, int(days or 1)))
    now_local = datetime.now(BEIJING)
    start_local = (now_local - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return start_local.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _connect(db: str):
    return sqlite3.connect("file:%s?mode=ro" % db, uri=True)


def _read_tokens(since_utc: str):
    con = _connect(NATIVE_DB)
    try:
        cur = con.cursor()
        cur.execute(
            """
            SELECT COALESCE(provider_model, provider_id, '未知模型'),
                   COALESCE(SUM(token_input_other), 0),
                   COALESCE(SUM(token_input_cached), 0),
                   COALESCE(SUM(token_output), 0),
                   COUNT(*)
            FROM provider_stats
            WHERE datetime(created_at) >= datetime(?)
            GROUP BY 1
            ORDER BY 2 + 3 + 4 DESC
            """,
            (since_utc,),
        )
        return cur.fetchall()
    finally:
        con.close()


def _all_time_total():
    con = _connect(NATIVE_DB)
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT COALESCE(SUM(token_input_other),0)+COALESCE(SUM(token_input_cached),0)"
            "+COALESCE(SUM(token_output),0), COUNT(*), MIN(created_at), MAX(created_at) FROM provider_stats"
        )
        total, n, first, last = cur.fetchone()
        return int(total or 0), int(n or 0), first, last
    finally:
        con.close()


def _read_cost(since_utc: str):
    """读 cost_control 的折算费用；不可用时返回 (None, 0)。"""
    try:
        con = _connect(COST_DB)
        try:
            cur = con.cursor()
            cur.execute(
                "SELECT COALESCE(SUM(cost_amount), 0), COUNT(*) FROM cost_supplements "
                "WHERE datetime(created_at) >= datetime(?)",
                (since_utc,),
            )
            total, n = cur.fetchone()
            return float(total or 0.0), int(n or 0)
        finally:
            con.close()
    except Exception:
        return None, 0


def _report(days: int) -> str:
    days = max(1, min(365, int(days or 1)))
    span = "今天" if days == 1 else "最近 %d 天" % days
    try:
        since = _window_start_utc(days)
        rows = _read_tokens(since)
        if not rows:
            total_all, n_all, first, last = _all_time_total()
            if n_all == 0:
                return "账本还是空的——至今没有任何调用记录。"
            return (
                "%s（北京时间）一口白饭都没吃到。\n"
                "不过历史累计吃掉 %s 白饭，共 %s 次调用（记录时间 %s ~ %s，UTC）。"
                % (span, _fmt(total_all), _fmt(n_all), first, last)
            )
    except Exception as e:  # noqa: BLE001
        logger.error("白饭账本读取 provider_stats 失败: %s", e)
        return "白饭账本读不出来了：" + str(e)

    total = 0
    lines = []
    for model, other, cached, out, calls in rows:
        s = int(other) + int(cached) + int(out)
        total += s
        lines.append(
            "- %s：%s 白饭（输入 %s ／ 缓存命中 %s ／ 输出 %s），%s 次调用"
            % (model, _fmt(s), _fmt(other), _fmt(cached), _fmt(out), _fmt(calls))
        )

    try:
        cost, n = _read_cost(since)
    except Exception:
        cost, n = None, 0

    head = "%s一共吃掉 %s 白饭（token）。" % (span, _fmt(total))
    if cost and cost > 0:
        tail = "折算下来大约 %.4f 元（按成本插件已配单价，%d 条计价记录）。" % (cost, n)
    else:
        tail = "金额还折算不出来——成本插件里没有为该模型配单价。"
    return head + "\n" + "\n".join(lines) + "\n" + tail


@register(
    "astrbot_plugin_white_rice",
    "本地",
    "白饭账本：把 token 用量以「白饭」的口吻汇报出来，支持自然语言与指令查询。",
    "1.1.0",
)
class WhiteRice(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    @llm_tool(name="white_rice_report")
    async def white_rice_report(self, event: AstrMessageEvent, days: int = 1) -> str:
        """查询本机器人消耗了多少 token，以及折算成多少钱。

        在本人设中，token 被称作「白饭」：「吃白饭」「吃了多少大白饭」都表示消耗 token。
        当用户问「你今天吃了多少大白饭」「吃了多少白饭」「今天用了多少 token」
        「这周消耗了多少」「花了多少钱」这类问题时，调用本工具获取真实数字，
        再用角色口吻回答，不要凭空编造用量。

        【必须每次都调用】用量一直在增长，所以每被问一次就要重新调用一次本工具：
        - 不要沿用对话历史里自己之前说过的数字（那是过期的）；
        - 不要沿用长期记忆里出现过的数字；
        - 尤其不要因为"刚才已经查过/回答过"就跳过调用、直接复述旧结论（例如「零」「没吃到」）。
          若历史上出现过「零」，那是当时还没有本工具时的错误回答，一律不可采信。

        Args:
            days(number): 可选。统计最近多少天，默认 1 表示今天；7 表示近一周。
        """
        _ = event
        return _report(days)

    @filter.command("白饭")
    async def white_rice_cmd(self, event: AstrMessageEvent):
        """指令形式查询白饭用量：/白饭 [天数]"""
        raw = (event.message_str or "").strip()
        parts = raw.split()
        days = 1
        if len(parts) >= 2 and parts[1].isdigit():
            days = int(parts[1])
        yield event.plain_result(_report(days))