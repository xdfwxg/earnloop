# SCAMS.md — AI 接活防骗库 / The anti-scam playbook

> Every pattern below cost someone real time or money. If it saved you once,
> star the repo and add what you saw — PRs welcome.
> 每一条都是真实学费换来的。帮到你一次就请点个 star，并把你的新发现提 PR。

## 1. 零奖金赏金仓（unfunded bounty）

**特征**：仓库自称"bounty"，README 或 issue 写着 "every bounty is unfunded at launch"，
只给"声望/曝光"。大量随机用户名账号排队认领，0 派单记录。
**代价**：一个 100 任务"赏金仓"可以消耗几百个 agent 数千小时。
**对策**：`earnloop` 规则 `unfunded` 直接归零分。

## 2. 占位符仓库（placeholder repo）

**特征**：README 里 Discord/联系方式是 `xxxx`、`TODO: add link`；仓库创建 < 7 天却挂着 $960 高额任务。
**对策**：`placeholder-repo` 规则 + 新仓库无 README 降权。

## 3. 先交钱才能开工（pay-to-work）

**特征**：押金 / 保证金 / 培训费 / "激活费"；常见话术"缴纳后可接高额单"。
**铁律**：任何合法平台，钱只从买方流向卖方。
**对策**：`pay-to-work` 硬拦截，不可 propose。

## 4. 脱离平台私下交易（off-platform escrow）

**特征**：让你加微信/QQ/Telegram 后转账、"平台抽成太高我们私下走"。
**代价**：脱离平台 = 失去担保，交付后拉黑。
**对策**：`off-platform-escrow` 硬拦截。首条沟通留在平台内。

## 5. 夸张预算（vague-huge-budget）

**特征**：budget unlimited / 上不封顶 / 日入过千。真客户预算写得具体，哪怕小。
**对策**：`vague-huge-budget` 归零分。

## 6. 机器刷量竞争（bot-flooded pools）

**特征**：公开池新单 12 分钟内 28 人投标，0 投标项目占比为 0。
**含义**：不是骗局，但期望值≈0。`earnloop` 用竞争度扣分，把注意力留给
投标 ≤ 10 或邀请制的池子。

## 维护约定

- 新模式必须附真实案例链接（脱敏），由维护者复现后合并。
- 规则在 `earnloop.py` 的 `SCAM_PATTERNS` 中，测试见 `tests/test_earnloop.py` 的 `ScamTest`。
- 误报（把真客户拦了）比漏报更伤——所以拦死只用前 5 条硬规则，其余只降权。
