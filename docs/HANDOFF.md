# ASTRA 接手记录（2026-10-25）

## 工作目录与分支

- 当前运行项目：`C:\Users\ATLAS\astra-quant`。
- 仓库：`XiaoyeCodes/astra-quant`；沿用 `fix/octobot-2.1.1-paper-e2e` 分支和 PR #1。
- `C:\Users\ATLAS\Documents\ChatGPT\Bitget量化` 是此前导入压缩包的另一个 checkout，本次没有修改它。

## Claude 已完成的部分

- OctoBot 2.1.1 接入修复和 Bitget 现货模拟盘端到端测试。
- 13 个预设、手动/AI 交易/AI 选策略/指标选策略四种方案，以及行情雷达。
- 主止损和 OctoBot 备用止损，均在模拟盘验证过；权益、重连及旧成交识别修复。
- 具体历史结果保留在 `docs/TEST_REPORT.md`。

## 本次完成

- 将用户指定的本机 DeepSeek 配置接入 `.env`，认证成功，使用 `deepseek-flash`。只使用 DeepSeek Key，没有将配置文件中的其他服务凭据写入项目。
- 修复短 JSON 请求的思考 token 预算问题、旧 AI 信号字段名、自动切换连续确认，以及不可信 AI 回答的校验。
- 实测 AI 雷达、OctoBot AI 信号、两次真实 AI 判断后的模拟盘自动切换；13 个无网络回归测试通过。
- 更新了测试报告，并清理测试期间 OctoBot 自动扩写的受版本控制模板。

## 交付时运行状态

- 驾驶舱：`http://localhost:8080`；OctoBot：`http://localhost:5001`。
- Bitget 公开接口，模拟盘；没有交易所交易密钥。
- Profile：`astra_idle`；方案：`manual`；最大止损 10%；原始起点 2205.56 USDT。2026-10-25 晚间测试和两次止损演练后，实际模拟余额约 2200.10 USDT，已核实全为 USDT，无挂单。
- DeepSeek Key 保存在 Git 忽略的 `.env`，不应输出、提交或写入文档。
- 测试的原始状态备份和无密钥 API 观察记录位于本机 `data/handoff-20261025-134033/`。

## 后续事项与边界

1. **AI 仓位**：DeepSeek 结构化请求已修复，技术分析智能体通过，但新闻/市值源返回 401，完整分配未通过。已在 `strategies.UNAVAILABLE_PRESETS` 禁用；修复并验证完整链路后再恢复。
2. **分批补仓止盈/止损**：真实行情模拟盘测试期间没有走完首单成交与退出链路。需要受控行情回放或继续观察模拟盘，不能将“已经挂单”算作退出已验证。
3. **指数持仓调仓**：已补齐 1h 策略事件依赖，22:33:06 实际买入 BTC、ETH、SOL，权重配置为 50/30/20，首轮调仓通过。
4. **休眠恢复**：没有添加后台重启任务。持续无法连接时，现有手动恢复命令是 `docker compose restart octobot`；启动时可能重新检查/安装插件，短暂 unhealthy 不等于已经卡死。
5. **策略参数**：网格、补仓比例、持仓上限及开新轮周期等仍沿用原参数；待用户决定的选项在测试报告第六节。本次修正 AI 置信度字段名是让原有 60% 设置生效。
6. **实盘**：桥接服务的策略切换、自动选策略和止损仍限模拟盘。没有启用实盘或更改相关限制。

## 回归验证

```powershell
python -m unittest discover -s tests -v
docker compose build bridge
docker compose up -d --no-deps bridge
```

服务重建后，用 `/api/state` 核对 `mode=paper`、`profile.id=astra_idle`、`strategy.scheme=manual`、`strategy.stopLoss.manual=10`，并确认没有挂单。不要用会展开环境变量值的 `docker compose config` 输出检查密钥。

## 2026-10-25 晚间 30 分钟观察更新

详细结果见 `SOAK_REPORT_2026-10-25.md`。主窗口 22:03:47–22:33:47，共 1800 秒；893 次有效采样；权益 2205.56 → 2201.28 USDT。之后进行了修复复测与主动止损演练，不能将演练交易当作策略业绩。

新增修复：断线保留真实快照、止损输入校验、DeepSeek JSON 输出兼容、指数触发依赖、插件版本元数据、跨策略模拟现货清算，以及初始化期间的基准保护。最终 30 项 Python、4 项前端回归通过。

从项目根目录使用 Docker Compose 启动，`tools/octobot_entrypoint.sh` 会调用有版本检查的补丁。它们作用于被忽略的上游插件文件，源码补丁和测试在 `tools/`、`tests/`。不要只复制修改后的 vendor 文件而遗漏启动适配。策略切换及方案启动在连接恢复后需要等待 60 秒，让资产估值初始化完成。

已知待查：模拟成交记录费用为约 0.2%，与预设 0.1% 不一致；旧页面重建期间曾有 348 次异常，仅保存到计数，新版观察未复现。抄底/反转未触发下单，补仓止盈止损仍未自然成交。休眠恢复仍未做专项测试。

## 2026-10-26 商业级审查与第一轮加固

详细见 `HARDENING_2026-10-26.md`。分支 `feat/commercial-hardening`。

- 新增：跨站/DNS 重绑定防护（`bridge/app/security.py`）、OctoBot 看门狗与优雅停机（`tools/octobot_supervisor.py`，入口脚本已改为调用它）、止损先清仓核实再暂停、模拟资金重置识别、手机通知（`bridge/app/notify.py`）、审计日志（`data/logs/`）、OctoBot 密码登录支持（`OCTOBOT_PASSWORD`）、固定依赖版本、GitHub Actions CI。
- 回归：`python -m unittest discover -s tests -v`（66 项）与 `node --test tests/test_feed.cjs`（4 项）。
- 交付时状态：模拟盘，profile `astra_idle`，方案 `manual`，止损 10%，起点 997.15 USDT（OctoBot 在重建容器时把模拟资金从约 2200 重置为 1000，见报告第二节第 4 条）。无挂单。
- 注意：bridge 现在只接受 `Host` 为 localhost/127.0.0.1/::1 的请求；写操作必须带 `Content-Type: application/json`。用 curl 调试写接口时要加这个头。
- 待用户决定：是否给 OctoBot 网页设密码；配置通知渠道；实盘风控是否开放。

## 2026-10-26 回测与持仓交接（分支 feat/backtest-handover，基于 feat/commercial-hardening）

- 回测引擎 `bridge/app/backtest.py`，数据工具 `tools/fetch_history.py`、`tools/run_backtest.py`；报告 `docs/BACKTEST_2026-10-26.md`；驾驶舱读取 `bridge/app/backtest_summary.json`（用 `run_backtest.py --summary` 重新生成）。
- 结论：两年里网格 +20%（回撤 13%），持有 BTC +16%（回撤 54%）；指标选策略 −34%~−64%，分批补仓 ASTRA 参数 −29%；高手续费下更差。
- 交接规则 `handover` 默认 `flatten_on_idle`，与止损共用“清仓 + 核实”流程；模拟盘下会等 OctoBot 保存账户后再重启（OctoBot 防抖 5 秒保存，重启从文件恢复）。
- 交付状态：模拟盘，`astra_idle`，只有 USDT 约 992，手动方案止损 10%。
- Python 测试 85 项。

## 2026-10-26 风控层与手续费（分支 feat/risk-layer，基于 feat/backtest-handover）

- 三条止损线（最大止损、回撤止损、单日亏损上限），共用“清仓核实”流程；止损后不再暂停交易者，而是切到观望并重启（暂停会导致 OctoBot 下次启动重置模拟资金）。
- 模拟盘手续费改为配置费率（启动补丁 `patch_paper_fees`，`tools/octobot_paper_fees.py`）；此前 ccxt 默认 0.2%。
- 详见 `docs/RISK_FEES_2026-10-26.md`。交付状态：观望，USDT 约 997.82，手动方案 10% / 8% / 4%。Python 测试 98 项。
